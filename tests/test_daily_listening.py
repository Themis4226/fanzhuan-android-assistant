"""Regression for a previous page's stale 'played once' badge."""
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import daily_listening

def page(n,badge):
    return {'width':1080,'height':2373,'elements':[
        {'text':'Listening','center':[540,175]},
        {'text':f'{n}/20','center':[766,2161]},
        {'text':badge,'center':[292,2075]}]}

class ListeningTransitionTests(unittest.TestCase):
    def intro(self,n=1):
        v=page(n,'已播放0遍')
        v['elements'] += [{'text':text,'center':[540,y],'confidence':.99} for text,y in [
            ('温馨提示',953),('温馨提示：当听力无法播放时，请先检查',1075),
            ('网络是否通畅或者删除小程序重新打开试',1122),('下哦~',1168),('确认',1408)]]
        return v

    def test_intro_handles_observed_line_wrap_and_scaled_phone_without_replaying_tap(self):
        for width,height in ((1080,2373),(720,1600)):
            v=self.intro()
            for e in v['elements']:
                e['center']=[round(e['center'][0]*width/1080),round(e['center'][1]*height/2373)]
            v.update(width=width,height=height)
            # OCR result order is not guaranteed; lines still compose the same dialog.
            v['elements'].reverse();tap=Mock();after=dict(v,elements=[e for e in v['elements'] if e['text'] not in {'温馨提示','确认'}
                                            and '温馨提示：' not in e['text'] and '网络是否' not in e['text'] and e['text']!='下哦~'])
            with patch.object(daily_listening.time,'sleep'):
                result=daily_listening.dismiss_intro(v,Mock(side_effect=[v,v,after]),tap,Mock())
            self.assertIs(result,after);self.assertEqual(tap.call_count,1)

    def test_intro_changed_page_or_unknown_dialog_never_confirms(self):
        for current in (self.intro(2),dict(self.intro(),elements=[e for e in self.intro()['elements'] if '网络是否通畅' not in e['text']])):
            tap=Mock()
            with self.assertRaises(RuntimeError):
                daily_listening.dismiss_intro(self.intro(),Mock(return_value=current),tap,Mock())
            tap.assert_not_called()
        tap=Mock()
        with patch.object(daily_listening.time,'sleep'),self.assertRaisesRegex(RuntimeError,'不重复点击'):
            daily_listening.dismiss_intro(self.intro(),Mock(return_value=self.intro()),tap,Mock())
        self.assertEqual(tap.call_count,1)

    def test_intro_on_first_and_later_page_still_requires_two_explicit_playbacks(self):
        frames=[self.intro(1),self.intro(1),page(1,'已播放0遍'),page(1,'已播放0遍'),page(1,'已播放第1遍'),
                self.intro(2),self.intro(2),page(2,'已播放0遍'),page(2,'已播放0遍'),page(2,'已播放第1遍'),
                {'width':1080,'height':2373,'elements':[{'text':'任务已完成','confidence':.99,'center':[540,1400]}]}]
        with TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'runs/session';folder.mkdir(parents=True)
            (root/'runs/active_daily_session.txt').write_text(str(folder),encoding='utf-8')
            (folder/'authorization.json').write_text('{"status":"active","automatic_answers":true}',encoding='utf-8')
            adb=Mock();adb.selector.return_value=('-t','1')
            with patch.object(daily_listening,'ROOT',root),patch.object(daily_listening,'Adb',return_value=adb),\
                 patch.object(daily_listening,'find_adb',return_value='adb'),patch.object(daily_listening,'parse_devices',return_value=[]),\
                 patch.object(daily_listening,'load_engine',return_value=SimpleNamespace()),\
                 patch.object(daily_listening,'fresh_capture',return_value=b'local-frame'),\
                 patch.object(daily_listening,'observe',side_effect=frames),patch.object(daily_listening,'require_wechat'),\
                 patch.object(daily_listening.time,'sleep'),patch('sys.argv',['daily_listening.py','--header','Listening','--count','2','--no-ocr-cache']),\
                 contextlib.redirect_stdout(io.StringIO()):daily_listening.main()
            self.assertEqual(adb.run.call_count,6)
            self.assertEqual([c.args[-2:] for c in adb.run.call_args_list],
                             [('540','1408'),('292','1975'),('999','2161')]*2)

    def test_observed_generic_card_completion_survives_title_change(self):
        for text in ('太棒了，卡包已完成~','当前卡包已完成','任务已完成'):
            v={'elements':[{'text':text,'confidence':.99}]}
            self.assertTrue(daily_listening.completed(v))
            v['elements'][0]['confidence']=.5
            self.assertFalse(daily_listening.completed(v))
        self.assertFalse(daily_listening.completed(page(12,'已播放第1遍')))

    def test_playback_help_dialog_requires_exact_observed_markers_and_one_button(self):
        def element(text,y):return {'text':text,'center':[540,y],'confidence':.99}
        v=page(1,'已播放0遍')
        v['elements'] += [element('温馨提示',950),element('温馨提示：当听力无法播放时，请先检查网',1073),
                          element('络是否通畅或者删除小程序重新打开试下哦',1119),element('确认',1406)]
        self.assertEqual(daily_listening.intro_confirm(v),[540,1406])
        title=next(e for e in v['elements'] if e['text']=='温馨提示');title['confidence']=.8861
        self.assertEqual(daily_listening.intro_confirm(v),[540,1406])
        title['confidence']=.7
        with self.assertRaises(RuntimeError):daily_listening.intro_confirm(v)
        title['confidence']=.99
        duplicate=dict(v,elements=v['elements']+[element('确认',1410)])
        with self.assertRaises(RuntimeError):daily_listening.intro_confirm(duplicate)
        unknown=dict(v,elements=[e for e in v['elements'] if '当听力无法播放时' not in e['text']])
        with self.assertRaises(RuntimeError):daily_listening.intro_confirm(unknown)
        self.assertIsNone(daily_listening.intro_confirm(page(1,'已播放0遍')))

    def test_stale_played_badge_does_not_skip_explicit_play(self):
        views=[page(1,'已播放第1遍'),page(1,'已播放0遍'),
               page(1,'第1遍播放中'),page(1,'已播放第1遍'),page(2,'已播放0遍')]
        with TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'runs/session';folder.mkdir(parents=True)
            (root/'runs/active_daily_session.txt').write_text(str(folder),encoding='utf-8')
            (folder/'authorization.json').write_text(json.dumps({'automatic_answers':True,'status':'active'}),encoding='utf-8')
            adb=Mock();adb.selector.return_value=('-t','1')
            with patch.object(daily_listening,'ROOT',root),patch.object(daily_listening,'Adb',return_value=adb),\
                 patch.object(daily_listening,'find_adb',return_value='adb'),patch.object(daily_listening,'parse_devices',return_value=[]),\
                 patch.object(daily_listening,'load_engine',return_value=SimpleNamespace()),\
                 patch.object(daily_listening,'fresh_capture',return_value=b'local-screenshot'),\
                 patch.object(daily_listening,'observe',side_effect=views),patch.object(daily_listening,'require_wechat'),\
                 patch.object(daily_listening.time,'sleep'),patch('sys.argv',['daily_listening.py','--header','Listening','--count','1','--no-ocr-cache']),\
                 contextlib.redirect_stdout(io.StringIO()):
                daily_listening.main()
            taps=[call.args for call in adb.run.call_args_list if 'tap' in call.args]
            self.assertEqual(len(taps),2)
            self.assertEqual(taps[0][-2:],('292','1975'))
            self.assertEqual(taps[1][-2:],('999','2161'))

if __name__=='__main__':unittest.main()
