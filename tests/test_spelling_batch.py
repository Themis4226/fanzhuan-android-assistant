"""Spelling batches reject ambiguous answers, stale reviews and partial input."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image,ImageDraw
from spelling_batch import choose_word,context,observed_bank,spelling_complete,completion_after_last_answer
from daily_spell import spelling_clues,has_progress


def element(text,x,y):
    return {'text':text,'center':[x,y],'bounds':[x-15,y-15,x+15,y+15],'confidence':.99}


class SpellingBatchTests(unittest.TestCase):
    def test_spelling_question_regions_scale_with_the_replacement_phone(self):
        for width,height in ((1080,2373),(720,1600)):
            v={'width':width,'height':height,'elements':[
                element('9/25',round(width*.04),round(height*.13)),
                element('n.合成释义',width//2,round(height*.285)),
                element('提示 reviewed',width//2,round(height*.48))]}
            self.assertTrue(has_progress(v,'9/25'));self.assertFalse(has_progress(v,'8/25'))
            self.assertEqual(spelling_clues(v),['n.合成释义'])
            v['elements'].append(dict(v['elements'][0]));self.assertFalse(has_progress(v,'9/25'))

    def state(self):return {'header':'syntheticcourse','clue':'评估','progress':'1/25','length':10}

    def test_exact_meaning_and_length_must_produce_one_candidate(self):
        state=self.state()
        self.assertEqual(choose_word(state,{'评估':{'evaluation','evaluate'}}),('evaluation','previous_green_feedback_definition'))
        self.assertIsNone(choose_word(state,{'评估':{'evaluation','assessment'}})[0])
        self.assertIsNone(choose_word(state,{'评估价格':{'evaluation'}})[0])

    def test_reviews_are_bound_to_the_current_question_and_valid_word(self):
        state=self.state();review=dict(state,source='assistant_review',word='evaluation')
        self.assertEqual(choose_word(state,{},[review]),('evaluation','assistant_review'))
        for key,value in [('header','other'),('clue','评价'),('progress','2/25'),('length',9),('source','model')]:
            with self.subTest(key=key):self.assertIsNone(choose_word(state,{},[dict(review,**{key:value})])[0])
        with self.assertRaises(RuntimeError):choose_word(state,{},[dict(review,word='eval')])

    def test_only_actual_green_feedback_supplies_the_observed_bank(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'runs'/'synthetic';folder.mkdir(parents=True)
            records=[{'word':'evaluation','definition':['A. n.评估'],'feedback':'green correct answer row observed'},
                     {'word':'assessment','definition':['n.评估'],'feedback':'model suggested'},
                     {'word':'bad;command','definition':['n.评估'],'feedback':'green correct answer row observed'}]
            (folder/'correct_word_answers.jsonl').write_text('\n'.join(json.dumps(x) for x in records),encoding='utf-8')
            self.assertEqual(observed_bank(root),{'评估':{'evaluation'}})

    def question(self,partial=False):
        image=Image.new('RGB',(1080,2373),'white');draw=ImageDraw.Draw(image)
        for i in range(10):draw.rectangle((120+i*80,1050,175+i*80,1054),fill='black')
        if partial:draw.rectangle((139,1000,146,1040),fill='black')
        output=BytesIO();image.save(output,format='PNG')
        v={'width':1080,'height':2373,'elements':[element('Synthetic course',540,177),element('1/25',45,310),element('n.评估',540,677)]}
        return output.getvalue(),v

    def test_empty_slots_are_read_but_existing_ink_is_never_appended_to(self):
        raw,v=self.question()
        state=context(raw,v)
        self.assertEqual((state['clue'],state['length'],state['progress']),('评估',10,'1/25'))
        with self.assertRaisesRegex(RuntimeError,'已有笔画'):context(self.question(True)[0],v)
        v['elements'].append(element('e',150,1010))
        with self.assertRaisesRegex(RuntimeError,'已有字母'):context(raw,v)

    def test_counter_uses_the_navigation_threshold_but_rejects_unclear_digits(self):
        raw,v=self.question();v['elements'][1]['confidence']=.8872
        self.assertEqual(context(raw,v)['progress'],'1/25')
        v['elements'][1]['confidence']=.7
        with self.assertRaisesRegex(RuntimeError,'进度不清晰'):context(raw,v)

    def test_completion_requires_matching_list_count_and_badge(self):
        v={'width':1080,'height':2373,'elements':[element('单词任务',540,177),element('单词拼写',200,500),
             element('25/25',200,650),element('已完成',920,650)]}
        self.assertTrue(spelling_complete(v))
        v['elements'][2]['text']='24/25';self.assertFalse(spelling_complete(v))
        v['elements'][2]['text']='25/25';v['elements'].pop();self.assertFalse(spelling_complete(v))

    def test_headerless_completion_is_only_valid_after_the_last_answer(self):
        v={'width':1080,'height':2373,'elements':[element('COMPLETE',540,1162),
           element('太棒了，任务已完成~',502,1386),element('完成',538,2042)]}
        self.assertTrue(completion_after_last_answer({'progress':'25/25','total':25},v))
        self.assertFalse(completion_after_last_answer({'progress':'24/25','total':25},v))
        v['elements'].append(element('完成',540,2040))
        self.assertFalse(completion_after_last_answer({'progress':'25/25','total':25},v))


if __name__=='__main__':unittest.main()
