"""Replacement preparation must never reset cost counters or inherit progress."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

import device_start as app
from phone_capture import ProbeError


class DeviceStartTests(unittest.TestCase):
    def prepare(self,tmp,status='active',serial='OLD'):
        root=Path(tmp);folder=root/'runs/source';folder.mkdir(parents=True)
        auth={'status':status,'serial':serial,'automatic_answers':True,'automatic_daily_submissions':True,
              'used_generation_calls':9,'max_new_deepseek_generation_calls':13}
        (folder/'authorization.json').write_text(json.dumps(auth),encoding='utf-8')
        (root/'runs/active_daily_session.txt').write_text(str(folder),encoding='utf-8')
        adb=Mock();adb.select_usb.return_value={'serial':'NEW','transport_id':'7'};adb.text.return_value='MODEL'
        adb.selector.return_value=('-t','7')
        return root,folder,adb

    def test_replacement_auto_binds_before_a_worker_and_keeps_remaining_calls(self):
        for status in ('active','completed'):
            with self.subTest(status=status),TemporaryDirectory() as tmp,redirect_stdout(StringIO()):
                root,source,adb=self.prepare(tmp,status)
                (source/'latest.png').write_bytes(b'OLD_PHONE_PROGRESS')
                folder,changed=app.prepare_session(root,adb)
                a=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
                self.assertTrue(changed);self.assertEqual((a['used_generation_calls'],a['max_new_deepseek_generation_calls']),(9,13))
                self.assertEqual(a['serial'],'NEW');self.assertFalse((folder/'latest.png').exists());adb.run.assert_not_called()

    def test_same_active_phone_uses_the_existing_session(self):
        with TemporaryDirectory() as tmp,redirect_stdout(StringIO()):
            root,source,adb=self.prepare(tmp,serial='NEW')
            folder,changed=app.prepare_session(root,adb)
            self.assertEqual(folder,source.resolve());self.assertFalse(changed)

    def test_same_completed_phone_does_not_replay_tasks(self):
        with TemporaryDirectory() as tmp:
            root,source,adb=self.prepare(tmp,'completed','NEW');original=(source/'authorization.json').read_bytes()
            with self.assertRaisesRegex(RuntimeError,'本次任务已完成'):app.prepare_session(root,adb)
            self.assertEqual((source/'authorization.json').read_bytes(),original)

    def test_closed_and_ungranted_sessions_are_not_silently_reopened(self):
        for status in ('closed','active'):
            with self.subTest(status=status),TemporaryDirectory() as tmp:
                root,source,adb=self.prepare(tmp,status)
                if status=='active':
                    a=json.loads((source/'authorization.json').read_text(encoding='utf-8'));a['automatic_answers']=False
                    (source/'authorization.json').write_text(json.dumps(a),encoding='utf-8')
                with self.assertRaises(RuntimeError):app.prepare_session(root,adb)
                adb.select_usb.assert_not_called()

    def test_unauthorized_or_multiple_usb_does_not_transfer_or_invoke_worker(self):
        for message in ('RSA unauthorized','multiple USB devices'):
            with self.subTest(message=message),TemporaryDirectory() as tmp:
                root,source,adb=self.prepare(tmp);adb.select_usb.side_effect=ProbeError(message)
                original=(source/'authorization.json').read_bytes()
                with self.assertRaises(ProbeError):app.prepare_session(root,adb)
                self.assertEqual((source/'authorization.json').read_bytes(),original)

    def test_wrapper_forwards_unicode_args_and_the_actual_worker_exit_code(self):
        with TemporaryDirectory() as tmp,redirect_stdout(StringIO()):
            root,source,adb=self.prepare(tmp,serial='NEW')
            with patch.object(app,'ROOT',root),patch.object(app,'Adb',return_value=adb),patch.object(app,'find_adb'),\
                 patch.object(app,'ensure_no_workers'),patch.object(app,'run_card_worker',return_value=(7,'中文错误')) as launch:
                self.assertEqual(app.main(['--','daily_listening.py','--header','中文 页面']),7)
            self.assertEqual(launch.call_args.args[0][-3:],[str(root/'daily_listening.py'),'--header','中文 页面'])

    def test_prepare_only_cannot_start_a_worker(self):
        with TemporaryDirectory() as tmp,redirect_stdout(StringIO()):
            root,source,adb=self.prepare(tmp,serial='NEW')
            with patch.object(app,'ROOT',root),patch.object(app,'Adb',return_value=adb),patch.object(app,'find_adb'),\
                 patch.object(app,'ensure_no_workers'),patch.object(app,'run_card_worker') as launch:
                self.assertEqual(app.main(['--prepare-only']),0)
            launch.assert_not_called()


if __name__=='__main__':unittest.main()
