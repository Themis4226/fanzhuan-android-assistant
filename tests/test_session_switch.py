from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import session_switch as app
from phone_capture import ProbeError


class PhoneSwitchTests(unittest.TestCase):
    def prepare(self, temporary):
        root=Path(temporary);old=root/'runs/old_phone';old.mkdir(parents=True)
        auth={'status':'active','serial':'OLD_PHONE','automatic_answers':True,'automatic_daily_submissions':True,
              'used_generation_calls':24,'max_new_deepseek_generation_calls':113,
              'prior_authorized_calls_used':87,'shared_authorized_call_limit':200,
              'arbitrary_choice_categories':['语法','阅读'],'skip_study_pages':True,
              'arbitrary_choice_headers':['Old course'], 'api_key':'SYNTHETIC_DO_NOT_COPY'}
        (old/'authorization.json').write_text(json.dumps(auth),encoding='utf-8')
        (old/'latest.png').write_bytes(b'SYNTHETIC_OLD_SCREEN')
        (old/'word_task_chain_result.json').write_text('{"status":"paused"}',encoding='utf-8')
        (root/'runs/active_daily_session.txt').write_text(str(old),encoding='utf-8')
        adb=Mock();adb.select_usb.return_value={'serial':'NEW_PHONE','transport_id':'2'}
        adb.selector.return_value=('-t','2');adb.text.return_value='SYNTHETIC_MODEL'
        return root,old,auth,adb

    def test_switch_preserves_budget_and_grants_without_copying_phone_progress_or_secrets(self):
        with TemporaryDirectory() as tmp:
            root,old,before,adb=self.prepare(tmp)
            folder,changed=app.switch_session(root,adb)
            auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
            self.assertTrue(changed);self.assertEqual(auth['serial'],'NEW_PHONE')
            for key in app.AUTH_FIELDS:
                if key in before:self.assertEqual(auth[key],before[key])
            self.assertEqual(auth['max_new_deepseek_generation_calls']-auth['used_generation_calls'],89)
            self.assertEqual(auth['arbitrary_choice_headers'],[]);self.assertNotIn('api_key',auth)
            self.assertFalse((folder/'latest.png').exists());self.assertFalse((folder/'word_task_chain_result.json').exists())
            self.assertEqual((old/'latest.png').read_bytes(),b'SYNTHETIC_OLD_SCREEN')
            self.assertEqual(json.loads((old/'authorization.json').read_text(encoding='utf-8'))['status'],'closed')
            self.assertEqual(Path((root/'runs/active_daily_session.txt').read_text(encoding='utf-8')),folder)
            adb.run.assert_not_called()

    def test_same_phone_is_idempotent_and_preserves_auth_bytes(self):
        with TemporaryDirectory() as tmp:
            root,old,before,adb=self.prepare(tmp)
            adb.select_usb.return_value={'serial':'OLD_PHONE'}
            data=(old/'authorization.json').read_bytes()
            folder,changed=app.switch_session(root,adb)
            self.assertFalse(changed);self.assertEqual(folder,old.resolve())
            self.assertEqual(data,(old/'authorization.json').read_bytes())
            self.assertEqual(len(list((root/'runs').iterdir())),2)

    def test_explicit_completed_phone_transfer_preserves_completion_and_spent_calls(self):
        with TemporaryDirectory() as tmp:
            root,old,before,adb=self.prepare(tmp);before.update(status='completed',completion_evidence='verified.json')
            path=old/'authorization.json';path.write_text(json.dumps(before),encoding='utf-8')
            with self.assertRaises(RuntimeError):app.switch_session(root,adb)
            folder,changed=app.switch_session(root,adb,allow_completed=True)
            source=json.loads(path.read_text(encoding='utf-8'));target=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
            self.assertTrue(changed);self.assertEqual(source['status'],'completed')
            self.assertEqual(source['completion_evidence'],'verified.json');self.assertTrue(source['budget_transferred_out'])
            self.assertEqual(target['status'],'active');self.assertEqual(target['used_generation_calls'],24)
            self.assertEqual(target['max_new_deepseek_generation_calls'],113)
            self.assertNotIn('completion_evidence',target);self.assertNotIn('budget_transferred_out',target)

    def test_completed_same_phone_does_not_reactivate_or_create_a_new_budget(self):
        with TemporaryDirectory() as tmp:
            root,old,before,adb=self.prepare(tmp);before['status']='completed'
            path=old/'authorization.json';path.write_text(json.dumps(before),encoding='utf-8');original=path.read_bytes()
            adb.select_usb.return_value={'serial':'OLD_PHONE'}
            folder,changed=app.switch_session(root,adb,allow_completed=True)
            self.assertFalse(changed);self.assertEqual(folder,old.resolve());self.assertEqual(path.read_bytes(),original)

    def test_venv_bootstrap_ancestor_is_ignored_but_sibling_and_parent_controller_are_not(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);command=str(root/'device_start.py')+' -- daily_tasks.py'
            records=[{'ProcessId':2,'ParentProcessId':1,'CommandLine':command},
                     {'ProcessId':3,'ParentProcessId':2,'CommandLine':command}]
            self.assertEqual(app.worker_conflicts(records,root,3,'device_start.py'),[])
            sibling=records+[{'ProcessId':4,'ParentProcessId':1,'CommandLine':str(root/'daily_words.py')}]
            self.assertEqual([r['ProcessId'] for r in app.worker_conflicts(sibling,root,3,'device_start.py')],[4])
            records[0]['CommandLine']=str(root/'daily_words.py')
            self.assertEqual([r['ProcessId'] for r in app.worker_conflicts(records,root,3,'device_start.py')],[2])

    def test_unauthorized_device_does_not_change_active_session(self):
        with TemporaryDirectory() as tmp:
            root,old,before,adb=self.prepare(tmp);data=(old/'authorization.json').read_bytes()
            adb.select_usb.side_effect=ProbeError('RSA unauthorized')
            with self.assertRaisesRegex(ProbeError,'unauthorized'):app.switch_session(root,adb)
            self.assertEqual(data,(old/'authorization.json').read_bytes())
            self.assertEqual(Path((root/'runs/active_daily_session.txt').read_text(encoding='utf-8')),old)

    def test_pointer_write_failure_rolls_back_original_authorization(self):
        with TemporaryDirectory() as tmp:
            root,old,before,adb=self.prepare(tmp);data=(old/'authorization.json').read_bytes()
            real=app.atomic_write
            def write(path,value):
                if path.name=='active_daily_session.txt':raise OSError('TEST_WRITE_FAILURE')
                real(path,value)
            with patch.object(app,'atomic_write',side_effect=write),self.assertRaisesRegex(OSError,'TEST_WRITE_FAILURE'):
                app.switch_session(root,adb)
            self.assertEqual(data,(old/'authorization.json').read_bytes())
            self.assertEqual(Path((root/'runs/active_daily_session.txt').read_text(encoding='utf-8')),old)
            for path in (root/'runs').glob('*/authorization.json'):
                if path.parent!=old:self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['status'],'closed')

    def test_closed_and_invalid_budget_sessions_cannot_be_transferred(self):
        for changed in ({'status':'closed'},{'used_generation_calls':114},{'used_generation_calls':-1}):
            with self.subTest(changed=changed),TemporaryDirectory() as tmp:
                root,old,auth,adb=self.prepare(tmp);auth.update(changed)
                (old/'authorization.json').write_text(json.dumps(auth),encoding='utf-8')
                with self.assertRaises(RuntimeError):app.switch_session(root,adb)
                adb.select_usb.assert_not_called()

    def test_repeated_switch_uses_current_spent_calls_instead_of_restoring_old_balance(self):
        with TemporaryDirectory() as tmp:
            root,old,_,adb=self.prepare(tmp)
            first,_=app.switch_session(root,adb)
            path=first/'authorization.json';auth=json.loads(path.read_text(encoding='utf-8'))
            auth['used_generation_calls']+=3;path.write_text(json.dumps(auth),encoding='utf-8')
            adb.select_usb.return_value={'serial':'THIRD_PHONE','transport_id':'3'}
            second,_=app.switch_session(root,adb)
            auth=json.loads((second/'authorization.json').read_text(encoding='utf-8'))
            self.assertEqual(auth['used_generation_calls'],27);self.assertEqual(auth['max_new_deepseek_generation_calls'],113)
            self.assertEqual(auth['serial'],'THIRD_PHONE')
            self.assertEqual(json.loads((first/'authorization.json').read_text(encoding='utf-8'))['status'],'closed')

    def test_changed_authorization_during_probe_is_not_overwritten(self):
        with TemporaryDirectory() as tmp:
            root,old,auth,adb=self.prepare(tmp)
            def select(devices,serial):
                if serial is not None:
                    auth['used_generation_calls']=25
                    (old/'authorization.json').write_text(json.dumps(auth),encoding='utf-8')
                return {'serial':'NEW_PHONE','transport_id':'2'}
            adb.select_usb.side_effect=select
            with self.assertRaisesRegex(RuntimeError,'会话在更换期间已变化'):app.switch_session(root,adb)
            self.assertEqual(json.loads((old/'authorization.json').read_text(encoding='utf-8'))['used_generation_calls'],25)
            self.assertEqual(Path((root/'runs/active_daily_session.txt').read_text(encoding='utf-8')),old)

    def test_worker_probe_blocks_other_task_but_ignores_its_own_process(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);records=[{'ProcessId':os.getpid()+10000,'CommandLine':str(root/'daily_tasks.py')}]
            with patch.object(app.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(records))):
                with self.assertRaisesRegex(RuntimeError,'仍在运行'):app.ensure_no_workers(root)
            records[0]['ProcessId']=os.getpid()
            with patch.object(app.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(records))):
                app.ensure_no_workers(root)


if __name__=='__main__':unittest.main()
