import copy
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, ImageDraw
from daily_words import question_signature, correct_rows, option_identity,record_reviewed_answer,reviewed_answer


class DailyWordValidationTests(unittest.TestCase):
    def test_reviewed_proposal_is_bound_to_exact_question_and_does_not_claim_submission(self):
        def e(text,x,y):return {'text':text,'center':[x,y],'bounds':[x-20,y-15,x+20,y+15],'confidence':.99}
        v={'width':1080,'height':2373,'elements':[e('Synthetic course卡包15',540,175),
            e('学习1/25',85,305),e('font',540,800),e('A. n.圣洗池',400,1260),e('B. n.清晰',400,1420)]}
        with TemporaryDirectory() as tmp:
            folder=Path(tmp);auth={'status':'active','automatic_answers':True,'used_generation_calls':26,'max_new_deepseek_generation_calls':100}
            (folder/'authorization.json').write_text(json.dumps(auth),encoding='utf-8')
            original=(folder/'authorization.json').read_bytes()
            record_reviewed_answer(folder,v,'A.n.圣洗池','Reviewed dictionary sense')
            proposal=json.loads((folder/'reviewed_answer_proposals.jsonl').read_text(encoding='utf-8'))
            self.assertFalse(proposal['submission_claimed'])
            self.assertEqual(reviewed_answer(folder,v,v['elements'][-2:])[0]['text'],'A. n.圣洗池')
            self.assertEqual(original,(folder/'authorization.json').read_bytes())
            for index,text in ((0,'Another course卡包15'),(1,'学习2/25'),(2,'front'),(3,'A. n.字体')):
                changed=copy.deepcopy(v);changed['elements'][index]['text']=text
                self.assertIsNone(reviewed_answer(folder,changed,changed['elements'][-2:]))

    def test_reviewed_proposal_rejects_closed_session_or_non_option_target(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'authorization.json').write_text('{"status":"closed"}',encoding='utf-8')
            with self.assertRaises(RuntimeError):record_reviewed_answer(folder,{},'完成','test')
            (folder/'authorization.json').write_text('{"status":"active","automatic_answers":true}',encoding='utf-8')
            v={'width':1080,'height':2373,'elements':[]}
            with self.assertRaises(RuntimeError):record_reviewed_answer(folder,v,'完成','test')

    def test_observed_greek_nu_pos_noise_does_not_invalidate_same_question(self):
        view={'width':1080,'height':2373,'elements':[
            {'text':'学习3/25','bounds':[0,279,170,332]},
            {'text':'fatten','bounds':[390,752,689,854]},
            {'text':'A.ν.（使）长胖，长肥；育肥','bounds':[121,1239,716,1291]},
            {'text':'B. n.不正常；越轨；偏离','bounds':[121,1394,628,1447]}]}
        live=copy.deepcopy(view);live['elements'][2]['text']='A.v.（使）长胖，长肥；育肥'
        self.assertEqual(question_signature(view),question_signature(live))
        self.assertEqual(option_identity(view['elements'][2]['text']),option_identity(live['elements'][2]['text']))
        live['elements'][2]['text']='A.n.（使）长胖，长肥；育肥'
        self.assertNotEqual(question_signature(view),question_signature(live))
        self.assertNotEqual(option_identity('A.vase'),option_identity('A.νase'))

    def test_normalization_still_rejects_changed_word_progress_or_meaning(self):
        base={'width':1080,'height':2373,'elements':[
            {'text':'学习3/25','bounds':[0,279,170,332]},
            {'text':'fatten','bounds':[390,752,689,854]},
            {'text':'A.v.（使）长胖，长肥；育肥','bounds':[121,1239,716,1291]}]}
        for index,text in ((0,'学习4/25'),(1,'fatigue'),(2,'A.v.减肥')):
            changed=copy.deepcopy(base);changed['elements'][index]['text']=text
            with self.subTest(text=text):self.assertNotEqual(question_signature(base),question_signature(changed))

    def test_icon_ocr_noise_does_not_change_question_identity(self):
        view={'width':1080,'height':2408,'elements':[
            {'text':'学习5/25','bounds':[0,269,170,320]},
            {'text':'incredibly','bounds':[200,800,800,1000]},
            {'text':'A. n.木头','bounds':[100,1300,400,1350]},
            {'text':'B. ad.难以置信地','bounds':[100,1450,600,1500]}]}
        noisy=copy.deepcopy(view)
        noisy['elements'].append({'text':'小','bounds':[600,1050,660,1100]})
        noisy['elements'][3]['text']='B.ad． 难以置信地'
        noisy['elements'].reverse()
        self.assertEqual(question_signature(view),question_signature(noisy))
        noisy['elements'][1]['text']='A. n.树木'
        self.assertNotEqual(question_signature(view),question_signature(noisy))

    def test_green_feedback_is_distinct_from_unanswered_or_red(self):
        options=[{'id':'t1','bounds':[100,600,500,650]}, {'id':'t2','bounds':[100,750,500,800]}]
        for color,expected in (('white',[]),((92,195,186),['t1']),((220,80,80),[])):
            image=Image.new('RGB',(1080,1200),'white')
            ImageDraw.Draw(image).rectangle((80,590,980,660),fill=color)
            stream=BytesIO(); image.save(stream,format='PNG')
            self.assertEqual([row['id'] for row in correct_rows(stream.getvalue(),options)],expected)


if __name__=='__main__':
    unittest.main()
