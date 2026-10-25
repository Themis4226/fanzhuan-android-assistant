import contextlib
import importlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import desktop_helper
import session_init


class DesktopSessionTests(unittest.TestCase):
    def prepare(self,tmp):
        root=Path(tmp);folder=root/'runs/current';folder.mkdir(parents=True)
        auth={'status':'active','automatic_answers':True,'used_generation_calls':7,
              'max_new_deepseek_generation_calls':13,'serial':'TEST','arbitrary_choice_headers':['Practice']}
        (folder/'authorization.json').write_text(json.dumps(auth),encoding='utf-8')
        (root/'runs/active_daily_session.txt').write_text(str(folder),encoding='utf-8')
        return root,folder

    def test_status_preserves_budget_and_contains_no_key_or_serial(self):
        with TemporaryDirectory() as tmp:
            root,folder=self.prepare(tmp);before=(folder/'authorization.json').read_bytes()
            (root/'.env.local').write_text('DEEPSEEK_API_KEY=TEST_PRIVATE_KEY',encoding='utf-8')
            result=desktop_helper.status(root)
            self.assertEqual(result['remaining'],6)
            self.assertNotIn('TEST_PRIVATE_KEY',json.dumps(result))
            self.assertNotIn('serial',result)
            self.assertEqual(before,(folder/'authorization.json').read_bytes())

    def test_close_preserves_calls_and_does_not_claim_task_completion(self):
        with TemporaryDirectory() as tmp:
            root,folder=self.prepare(tmp);desktop_helper.close_session(root)
            auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
            self.assertEqual(auth['status'],'closed');self.assertEqual(auth['used_generation_calls'],7)
            self.assertEqual(auth['max_new_deepseek_generation_calls'],13)
            event=json.loads((folder/'events.jsonl').read_text(encoding='utf-8'))
            self.assertFalse(event['learning_completion_claimed'])

    def test_foreign_pointer_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runs').mkdir()
            (root/'runs/active_daily_session.txt').write_text(str(root/'foreign'),encoding='utf-8')
            with self.assertRaises(RuntimeError):desktop_helper.status(root)

    def test_closed_session_blocks_every_task_before_adb_or_model(self):
        cases=[('daily_words',[]),('daily_word_tasks',[]),('daily_listening',['--header','Practice']),
               ('daily_choice_pages',['--header','Practice']),('daily_spell',['--word','cat','--clue','猫','--progress','1/25']),
               ('daily_suggest',[]),('daily_navigate',['--tap','完成']),('daily_navigate',['--close-completion'])]
        for name,args in cases:
            with self.subTest(script=name,args=args),TemporaryDirectory() as tmp:
                root,folder=self.prepare(tmp);desktop_helper.close_session(root);mod=importlib.import_module(name)
                with patch.object(mod,'ROOT',root),patch.object(mod,'Adb') as adb,patch('sys.argv',[name,*args]):
                    with self.assertRaises(RuntimeError):mod.main()
                    adb.assert_not_called()

    def test_closed_session_can_be_replaced_by_explicit_new_session(self):
        with TemporaryDirectory() as tmp:
            root,folder=self.prepare(tmp);desktop_helper.close_session(root)
            with patch.object(session_init,'ROOT',root),patch.object(session_init,'Adb') as cls,\
                 patch.object(session_init,'find_adb'),patch.object(session_init,'parse_devices'),\
                 patch('sys.argv',['session_init']),contextlib.redirect_stdout(io.StringIO()):
                cls.return_value.select_usb.return_value={'serial':'NEW_TEST_USB'};session_init.main()
            self.assertNotEqual(str(folder),(root/'runs/active_daily_session.txt').read_text(encoding='utf-8'))
            self.assertEqual(json.loads((folder/'authorization.json').read_text(encoding='utf-8'))['status'],'closed')


if __name__=='__main__':unittest.main()
