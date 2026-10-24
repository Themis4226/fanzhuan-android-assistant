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
                 patch.object(daily_listening.time,'sleep'),patch('sys.argv',['daily_listening.py','--header','Listening','--count','1']),\
                 contextlib.redirect_stdout(io.StringIO()):
                daily_listening.main()
            taps=[call.args for call in adb.run.call_args_list if 'tap' in call.args]
            self.assertEqual(len(taps),2)
            self.assertEqual(taps[0][-2:],('292','1975'))
            self.assertEqual(taps[1][-2:],('999','2161'))

if __name__=='__main__':unittest.main()
