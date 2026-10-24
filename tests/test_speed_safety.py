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
from daily_spell import type_letters
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
