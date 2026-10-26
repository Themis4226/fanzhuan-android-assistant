"""Speed regressions: a fresh changed frame must never reuse a stale decision."""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from daily_privacy import assert_no_notification
from daily_spell import type_letters,type_word,clear_rejected_case,input_letters,shift_center
import daily_words
import daily_choice_pages
import daily_listening
import numpy as np
from fast_observer import ExactFrameObserver


class ExactFrameTests(unittest.TestCase):
    def test_identical_frame_reuses_ocr_but_returns_isolated_current_metadata(self):
        fn=Mock(return_value={'elements':[{'text':'A. 猫'}], 'live_capture':False})
        observer=ExactFrameObserver(SimpleNamespace(use_cls=False),fn)
        first=observer(b'frame',live=False)
        first['elements'][0]['text']='changed by request builder'
        second=observer(b'frame',live=True)
        self.assertEqual(fn.call_count,1)
        self.assertEqual(second['elements'][0]['text'],'A. 猫')
        self.assertTrue(second['live_capture'])
        self.assertIn('observed_at',second)

    def test_changed_frame_including_notification_is_recognized_and_blocked(self):
        fn=Mock(side_effect=[{'elements':[]},{'elements':[{'text':'刚刚','center':[100,200]}]}])
        observer=ExactFrameObserver(SimpleNamespace(),fn)
        assert_no_notification(observer(b'original frame'))
        with self.assertRaisesRegex(RuntimeError,'Notification'):
            assert_no_notification(observer(b'new notification overlay'))
        self.assertEqual(fn.call_count,2)

    def test_roi_orientation_and_cache_eviction_force_new_ocr(self):
        fn=Mock(return_value={'elements':[]})
        engine=SimpleNamespace(use_cls=False)
        observer=ExactFrameObserver(engine,fn)
        observer(b'a');observer(b'a',[0,0,20,20])
        engine.use_cls=True
        observer(b'a',[0,0,20,20]);observer(b'a')
        self.assertEqual(fn.call_count,4)
        self.assertEqual(len(observer._frames),2)

    def test_disabled_cache_and_failed_ocr_never_reused(self):
        fn=Mock(side_effect=[RuntimeError('OCR failed'),{'elements':[]},{'elements':[]}])
        observer=ExactFrameObserver(SimpleNamespace(),fn,enabled=False)
        with self.assertRaises(RuntimeError):observer(b'a')
        observer(b'a');observer(b'a')
        self.assertEqual(fn.call_count,3)


class SpellingTransportTests(unittest.TestCase):
    def test_initial_capital_verifies_shift_first_letter_and_lowercase_rest(self):
        def keyboard(case,typed=''):
            letters='qwertyasdfgzxcv';letters=letters.upper() if case=='upper' else letters
            elements=[{'text':c,'center':[100+i*40,1740],'confidence':.99} for i,c in enumerate(letters)]
            if typed:elements.append({'text':typed,'center':[190,864],'confidence':.99})
            return {'width':1080,'height':2373,'elements':elements}
        centers={c:(100+i*40,1740) for i,c in enumerate('facebook')};centers['shift']=(84,2046)
        for auto_lower in (False,True):
            adb=Mock();views=[keyboard('upper'),keyboard('lower' if auto_lower else 'upper','F')]
            if not auto_lower:views.append(keyboard('lower','F'))
            with patch('daily_spell.time.sleep'):
                type_word(adb,('-t','1'),'Facebook',centers,1080,2373,read=Mock(side_effect=views),
                          initial_view=keyboard('lower'),guard=Mock())
            self.assertEqual(adb.run.call_count,3 if auto_lower else 4)
            self.assertEqual(adb.run.call_args_list[0].args[-2:],('84','2046'))
            self.assertNotIn('confirm',adb.run.call_args.args[-1])
        for word in ('F;reboot','F acebook','FACEBOOK','Facebook\n'):
            adb=Mock()
            with self.assertRaises(ValueError):type_word(adb,('-t','1'),word,{},1080,2373)
            adb.run.assert_not_called()

    def test_capital_input_never_types_when_shift_does_not_change_keyboard(self):
        def keyboard():return {'width':1080,'height':2373,'elements':[
            {'text':c,'center':[100+i*40,1740],'confidence':.99} for i,c in enumerate('qwerty')]}
        centers={c:(100+i*40,1740) for i,c in enumerate('facebook')};centers['shift']=(84,2046);adb=Mock()
        with patch('daily_spell.time.sleep'),self.assertRaisesRegex(RuntimeError,'Shift did not'):
            type_word(adb,('-t','1'),'Facebook',centers,1080,2373,read=keyboard,initial_view=keyboard(),guard=Mock())
        self.assertEqual(adb.run.call_count,1)

    def test_shift_key_requires_a_unique_observed_gray_boundary(self):
        image=np.full((2373,1080,3),255,dtype=np.uint8);image[1979:2114,12:156]=[187,175,168]
        self.assertEqual(shift_center(image),(84,2046))
        image[:]=255
        with self.assertRaises(RuntimeError):shift_center(image)

    def test_rejected_case_clear_requires_the_exact_hint_and_existing_input(self):
        def element(text,x,y):return {'text':text,'center':[x,y],'confidence':.99}
        v={'width':1080,'height':2373,'elements':[element(c,190+i*100,870) for i,c in enumerate('faCebOO')]
           +[element('k',190,1026),element('提示 Facebook',272,1144),element('X',1003,2043)]}
        self.assertEqual(input_letters(v).lower(),'facebook')
        adb=Mock();clear_rejected_case(adb,('-t','1'),v,'Facebook')
        self.assertEqual(adb.run.call_args.args[-1].count('input tap 1003 2043'),8)
        for bad in (dict(v,elements=v['elements'][:-1]),dict(v,elements=v['elements'][1:]),
                    dict(v,elements=[e for e in v['elements'] if not e['text'].startswith('提示')])):
            adb=Mock()
            with self.assertRaises(RuntimeError):clear_rejected_case(adb,('-t','1'),bad,'Facebook')
            adb.run.assert_not_called()

    def test_one_round_trip_contains_letters_only_and_stops_on_failure(self):
        adb=Mock()
        type_letters(adb,('-t','1'),'cat',{'c':(30,70),'a':(10,60),'t':(50,50)},100,100)
        adb.run.assert_called_once_with('-t','1','shell',
            'input tap 30 70 && sleep 0.06 && input tap 10 60 && sleep 0.06 && input tap 50 50')

    def test_bad_word_or_coordinates_do_not_send_partial_input(self):
        for word,centers in [('ca;',{}),('cat',{'c':(30,70),'a':(10,60),'t':(100,50)}),
                             ('cat',{'c':(30,70),'a':(10,60),'t':('50;reboot',50)})]:
            adb=Mock()
            with self.subTest(word=word,centers=centers),self.assertRaises((ValueError,KeyError)):
                type_letters(adb,('-t','1'),word,centers,100,100)
            adb.run.assert_not_called()


def word_page(word):
    return {'width':1080,'height':2373,'elements':[
        {'text':'卡包13','bounds':[20,150,200,200],'center':[110,175]},
        {'text':word,'bounds':[200,800,800,900],'center':[500,850],'confidence':.99}]}


class WordLoopTests(unittest.TestCase):
    def run_skip(self,views,count):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);folder=root/'runs/session';folder.mkdir(parents=True)
            (root/'runs/active_daily_session.txt').write_text(str(folder),encoding='utf-8')
            (folder/'authorization.json').write_text(json.dumps({'automatic_answers':True,'status':'active'}),encoding='utf-8')
            adb=Mock();adb.selector.return_value=('-t','1')
            values={'ROOT':root,'Adb':Mock(return_value=adb),'find_adb':Mock(return_value='adb'),
                    'parse_devices':Mock(return_value=[]),'load_engine':Mock(return_value=SimpleNamespace()),
                    'fresh_capture':Mock(side_effect=[f'frame{i}'.encode() for i in range(len(views))]),
                    'observe':Mock(side_effect=views),'require_wechat':Mock(),
                    'call_deepseek':Mock(side_effect=AssertionError('No API calls expected'))}
            for key,val in values.items():stack.enter_context(patch.object(daily_words,key,val))
            stack.enter_context(patch.object(daily_words.time,'sleep'))
            stack.enter_context(patch('sys.argv',['daily_words.py','--skip-review','--count',str(count)]))
            stack.enter_context(redirect_stdout(StringIO()))
            daily_words.main()
            return values,adb

    def test_five_definition_advances_use_six_reads_instead_of_eleven(self):
        values,adb=self.run_skip([word_page(w) for w in ['cat','dog','bird','pig','cow','horse']],5)
        self.assertEqual(values['fresh_capture'].call_count,6)
        self.assertEqual(values['observe'].call_count,6)
        self.assertEqual(adb.run.call_count,5)
        values['call_deepseek'].assert_not_called()

    def test_unchanged_page_stops_after_one_swipe(self):
        with self.assertRaisesRegex(RuntimeError,'did not advance'):
            self.run_skip([word_page('cat'),word_page('cat')],5)


class PageLoopTests(unittest.TestCase):
    def run_worker(self,module,views,argv,extra=None):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);folder=root/'runs/session';folder.mkdir(parents=True)
            (root/'runs/active_daily_session.txt').write_text(str(folder),encoding='utf-8')
            (folder/'authorization.json').write_text(json.dumps({'automatic_answers':True,
                'status':'active','arbitrary_choice_headers':['Practice']}),encoding='utf-8')
            adb=Mock();adb.selector.return_value=('-t','1')
            values={'ROOT':root,'Adb':Mock(return_value=adb),'find_adb':Mock(return_value='adb'),
                    'parse_devices':Mock(return_value=[]),'load_engine':Mock(return_value=SimpleNamespace()),
                    'fresh_capture':Mock(side_effect=[f'frame{i}'.encode() for i in range(len(views))]),
                    'observe':Mock(side_effect=views),'require_wechat':Mock()}
            values.update(extra or {})
            for key,val in values.items():stack.enter_context(patch.object(module,key,val))
            stack.enter_context(patch.object(module.time,'sleep'))
            stack.enter_context(patch('sys.argv',[module.__name__,*argv]))
            stack.enter_context(redirect_stdout(StringIO()))
            module.main()
            return values,adb

    def test_two_choice_pages_use_five_reads_and_preserve_feedback_check(self):
        def page(n):
            return {'width':1080,'height':2373,'elements':[
                {'text':'Practice','bounds':[20,150,200,200],'center':[110,175]},
                {'text':f'{n}/10','bounds':[600,2200,800,2260],'center':[700,2230]},
                {'text':'A','bounds':[70,800,130,860],'center':[100,830],'confidence':.99}]}
        green=np.zeros((2373,1080,3),dtype=np.uint8);green[:]=[186,195,92]
        values,adb=self.run_worker(daily_choice_pages,[page(1),page(1),page(2),page(2),page(3)],
            ['--header','Practice','--count','2'],{'decode_image':Mock(return_value=green)})
        self.assertEqual(values['fresh_capture'].call_count,5)
        self.assertEqual(values['decode_image'].call_count,2)
        self.assertEqual(adb.run.call_count,4)

    def test_unselected_choice_feedback_never_advances(self):
        v={'width':1080,'height':2373,'elements':[
            {'text':'Practice','bounds':[20,150,200,200],'center':[110,175]},
            {'text':'1/10','bounds':[600,2200,800,2260],'center':[700,2230]},
            {'text':'A','center':[100,830],'confidence':.99}]}
        with self.assertRaisesRegex(RuntimeError,'No selected-answer feedback'):
            self.run_worker(daily_choice_pages,[v,v],['--header','Practice','--count','2'],
                {'decode_image':Mock(return_value=np.full((2373,1080,3),255,dtype=np.uint8))})

    def test_each_listening_page_still_requires_explicit_play_and_increment(self):
        def page(n,badge):
            return {'width':1080,'height':2373,'elements':[
                {'text':'Practice','center':[540,175]},
                {'text':f'{n}/20','center':[766,2161]},
                {'text':badge,'center':[292,2075]}]}
        views=[]
        for n in (1,2):
            views.extend([page(n,'已播放第1遍'),page(n,'已播放0遍'),
                          page(n,'第1遍播放中'),page(n,'已播放第1遍')])
        views.append(page(3,'已播放0遍'))
        values,adb=self.run_worker(daily_listening,views,['--header','Practice','--count','2'])
        self.assertEqual(values['fresh_capture'].call_count,9)
        taps=[call.args[-2:] for call in adb.run.call_args_list]
        self.assertEqual(taps,[('292','1975'),('999','2161')]*2)


if __name__=='__main__':unittest.main()
