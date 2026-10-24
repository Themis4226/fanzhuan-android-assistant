import copy
from io import BytesIO
import unittest

from PIL import Image, ImageDraw
from daily_words import question_signature, correct_rows


class DailyWordValidationTests(unittest.TestCase):
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
