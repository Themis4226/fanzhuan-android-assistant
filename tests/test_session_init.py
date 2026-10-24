import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch
import session_init

class SessionInitializationTests(unittest.TestCase):
    def test_default_session_has_zero_api_budget_and_no_automatic_answers(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);adb=Mock();adb.select_usb.return_value={'serial':'SYNTHETIC_USB'}
            with patch.object(session_init,'ROOT',root),patch.object(session_init,'Adb',return_value=adb),\
                 patch.object(session_init,'find_adb'),patch.object(session_init,'parse_devices'),\
                 patch('sys.argv',['session_init.py']),contextlib.redirect_stdout(io.StringIO()):
                session_init.main()
            folder=Path((root/'runs/active_daily_session.txt').read_text(encoding='utf-8'))
            auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
            self.assertEqual(auth['max_new_deepseek_generation_calls'],0)
            self.assertFalse(auth['automatic_answers'])
            self.assertEqual(auth['serial'],'SYNTHETIC_USB')
            self.assertFalse((root/'.env.local').exists())

    def test_paid_budget_requires_explicit_flags_before_device_access(self):
        with TemporaryDirectory() as tmp,patch.object(session_init,'ROOT',Path(tmp)),\
             patch.object(session_init,'Adb') as adb,patch('sys.argv',['session_init.py','--api-call-limit','1']),\
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):session_init.main()
            adb.assert_not_called()
            self.assertFalse((Path(tmp)/'runs').exists())

    def test_active_session_cannot_be_replaced(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'runs/original';folder.mkdir(parents=True)
            (folder/'authorization.json').write_text('{"status":"active"}',encoding='utf-8')
            active=root/'runs/active_daily_session.txt';active.write_text(str(folder),encoding='utf-8')
            with patch.object(session_init,'ROOT',root),patch('sys.argv',['session_init.py']),patch.object(session_init,'Adb') as adb:
                with self.assertRaises(RuntimeError):session_init.main()
                adb.assert_not_called()
            self.assertEqual(active.read_text(encoding='utf-8'),str(folder))

if __name__=='__main__':unittest.main()
