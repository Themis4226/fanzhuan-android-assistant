"""Spelling entry, persistent runtime, quota, and completion regressions. Offline."""
from contextlib import redirect_stdout,ExitStack
from io import BytesIO,StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from PIL import Image,ImageDraw

from daily_spell import SpellingRuntime,spelling_clues
from phone_capture import RunLog
from spelling_batch import run_batch,observed_bank,context
from daily_words import chinese
from spelling_navigation import spelling_entry,spelling_page,open_spelling,finish_spelling
from spelling_suggest import question_id,build_spelling_request,validate_spelling_response,suggest_spelling
import daily_word_tasks as chain
from daily_tasks import DailyCoordinator
import daily_navigation as nav


def element(text,x,y):
    return {'text':text,'confidence':.99,'center':[x,y],'bounds':[x-18,y-15,x+18,y+15]}


def listing(done=0,total=2,width=1080,height=2376):
    e=lambda text,x,y:element(text,round(width*x),round(height*y))
    return {'width':width,'height':height,'elements':[e('单词任务',.5,.075),
        e('四级写作词汇（新）',.28,.286),e('单词拼写',.56,.286),
        e(f'{done}/{total}',.32,.347),e('已完成' if done==total else '开始',.86,.36)]}


def question(n,total=2,clue='n.猫',typed=None,course='四级写作词汇（新）',slots=3):
    image=Image.new('RGB',(1080,2376),'white');draw=ImageDraw.Draw(image)
    for i in range(slots):draw.rectangle((120+i*80,1050,175+i*80,1054),fill='black')
    f=BytesIO();image.save(f,format='PNG')
    v={'width':1080,'height':2376,'elements':[element(course,540,177),
         element(f'{n}/{total}',43,310),element(clue,540,677),element('确认',931,2203)]}
    for keys,x,y in [('qwertyuiop',60,1740),('asdfghjkl',114,1898),('zxcvbnm',220,2050)]:
        v['elements'].extend(element(key,x+i*106,y) for i,key in enumerate(keys))
    if typed:v['elements'].append(element(typed,260,900))
    return f.getvalue(),v


def completed():
    return {'width':1080,'height':2376,'elements':[element('COMPLETE',540,1162),
         element('太棒了，任务已完成~',502,1386),element('完成',538,2042)]}


class SpellingEntryTests(unittest.TestCase):
    def test_entry_is_associated_with_its_course_counter_and_button_at_two_sizes(self):
        for size in ((1080,2376),(720,1600)):
            entry=spelling_entry(listing(width=size[0],height=size[1]))
            self.assertEqual((entry['header'],entry['done'],entry['total'],entry['completed']),
                             ('四级写作词汇（新）',0,2,False))
        self.assertTrue(spelling_entry(listing(2))['completed'])

    def test_ambiguous_or_inconsistent_row_never_becomes_a_start_button(self):
        for mutation in ('count','course','button'):
            v=listing()
            if mutation=='count':v['elements'].append(element('1/2',350,824))
            elif mutation=='course':v['elements'].append(element('另一写作词汇',305,680))
            else:v['elements'][-1]['text']='已完成'
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):spelling_entry(v)

    def test_open_requires_unchanged_row_and_expected_course_and_progress(self):
        v=listing();q=question(1)[1];tap=Mock()
        with patch('word_navigation.time.sleep'):
            self.assertIs(open_spelling(spelling_entry(v),Mock(side_effect=[v,q]),tap,Mock()),q)
        self.assertEqual(tap.call_count,1)
        tap=Mock()
        with self.assertRaisesRegex(RuntimeError,'入口'):
            open_spelling(spelling_entry(v),Mock(return_value=listing(1)),tap,Mock())
        tap.assert_not_called()
        with patch('word_navigation.time.sleep'),self.assertRaisesRegex(RuntimeError,'一致'):
            open_spelling(spelling_entry(v),Mock(side_effect=[v]+[question(2)[1]]*4),Mock(),Mock())

    def test_completion_needs_last_answer_and_full_matching_list(self):
        state={'header':'四级写作词汇新','total':2,'progress':'2/2'};tap=Mock()
        with patch('word_navigation.time.sleep'):
            after=finish_spelling(completed(),state,Mock(side_effect=[completed(),listing(2)]),tap,Mock())
        self.assertTrue(spelling_entry(after)['completed']);tap.assert_called_once_with([538,2042])
        with patch('word_navigation.time.sleep'),self.assertRaisesRegex(RuntimeError,'未证实'):
            finish_spelling(completed(),state,Mock(side_effect=[completed(),listing(1)]),Mock(),Mock())
        with self.assertRaisesRegex(RuntimeError,'最后一道'):
            finish_spelling(completed(),dict(state,progress='1/2'),Mock(),Mock(),Mock())


class PersistentSpellingTests(unittest.TestCase):
    def test_wrapped_cherish_tail_uses_identical_request_and_input_rules(self):
        clue='vt.珍爱；vt.怀有（感情等）；抱有（希望'
        def frame(typed=None):
            raw,v=question(21,total=25,clue=clue,typed=typed,slots=7)
            v['elements'].append(dict(element('等)',528,729),confidence=.8195))
            v['elements'].reverse()  # Request and guard also share the same order.
            return raw,v
        raw,v=frame();state=context(raw,v)
        self.assertEqual(state['raw_clue'],clue)
        self.assertEqual(state['clue'],chinese(''.join(spelling_clues(v))))
        with TemporaryDirectory() as tmp,redirect_stdout(StringIO()),patch('daily_spell.time.sleep'),patch('spelling_suggest.call_deepseek') as api:
            adb=Mock();adb.log=RunLog(Path(tmp))
            read=Mock(side_effect=[(raw,v),frame('cherish'),question(22,total=25)])
            runtime=SpellingRuntime(adb,('-t','1'),read,Mock())
            runtime.answer('cherish',state['raw_clue'],state['progress'],expected_header=state['header'])
            self.assertEqual(adb.run.call_count,2);self.assertEqual(read.call_count,3)
            api.assert_not_called()

    def test_same_page_transient_clue_misread_is_rechecked_before_any_input_without_api(self):
        with TemporaryDirectory() as tmp,redirect_stdout(StringIO()),patch('daily_spell.time.sleep'),patch('spelling_suggest.call_deepseek') as api:
            adb=Mock();adb.log=RunLog(Path(tmp))
            read=Mock(side_effect=[question(1,clue='n.狗'),question(1),question(1,typed='cat'),question(2)])
            runtime=SpellingRuntime(adb,('-t','1'),read,Mock())
            runtime.answer('cat','n.猫','1/2',expected_header='四级写作词汇（新）')
            self.assertEqual(read.call_count,4);self.assertEqual(adb.run.call_count,2)
            api.assert_not_called()

    def test_persistent_clue_change_rechecks_three_times_then_stops_without_input(self):
        with TemporaryDirectory() as tmp,patch('daily_spell.time.sleep'):
            adb=Mock();adb.log=RunLog(Path(tmp));read=Mock(side_effect=[question(1,clue='n.狗')]*3)
            runtime=SpellingRuntime(adb,('-t','1'),read,Mock())
            with self.assertRaisesRegex(RuntimeError,'连续3次不一致'):runtime.answer('cat','n.猫','1/2')
            self.assertEqual(read.call_count,3);adb.run.assert_not_called()

    def test_course_or_progress_change_during_recheck_never_receives_input(self):
        for changed in (question(2),question(1,course='另一英语课程')):
            with TemporaryDirectory() as tmp,patch('daily_spell.time.sleep'):
                adb=Mock();adb.log=RunLog(Path(tmp))
                read=Mock(side_effect=[question(1,clue='n.狗'),changed])
                runtime=SpellingRuntime(adb,('-t','1'),read,Mock())
                with self.assertRaisesRegex(RuntimeError,'进度变化|课程在输入前变化'):
                    runtime.answer('cat','n.猫','1/2',expected_header='四级写作词汇（新）')
                self.assertEqual(read.call_count,2);adb.run.assert_not_called()

    def test_delayed_model_answer_cannot_move_to_another_course_with_same_clue_and_counter(self):
        adb=Mock();read=Mock(return_value=question(1,course='另一英语课程'))
        runtime=SpellingRuntime(adb,('-t','1'),read,Mock())
        with self.assertRaisesRegex(RuntimeError,'课程在输入前变化'):
            runtime.answer('cat','n.猫','1/2',expected_header='四级写作词汇（新）')
        adb.run.assert_not_called()

    def test_daily_coordinator_resumes_a_spelling_page_before_dashboard_inventory(self):
        elements=[element('今日任务',540,177),element('3/3',915,370),element('100%',610,297)]
        for i,category in enumerate(nav.CATEGORIES):
            x=180+(i%3)*330;y=700+(i//3)*430
            elements.extend([element(category,x,y),element('已完成3/3' if category=='单词' else '已完成0/0',x,y+50)])
        dashboard={'width':1080,'height':2376,'elements':elements}
        worker=Mock(return_value=(0,''));save=Mock()
        coordinator=DailyCoordinator(Mock(side_effect=[question(1)[1],dashboard]),Mock(),Mock(),worker,
                                     Mock(),Mock(),save,resume_word=lambda _:False)
        with redirect_stdout(StringIO()):coordinator.run()
        self.assertEqual(worker.call_count,1)
        self.assertEqual(worker.call_args.args[0][0],'daily_word_tasks.py')
        self.assertEqual(coordinator.report['status'],'all_daily_tasks_verified')

    def test_word_chain_enters_spelling_without_numbered_cards_then_returns_to_dashboard(self):
        with TemporaryDirectory() as tmp,redirect_stdout(StringIO()),ExitStack() as stack:
            root=Path(tmp);folder=root/'runs/session';folder.mkdir(parents=True)
            (root/'runs/active_daily_session.txt').write_text(str(folder),encoding='utf-8')
            (folder/'authorization.json').write_text('{"status":"active","automatic_answers":true}',encoding='utf-8')
            answers=[{'word':w,'definition':[m],'feedback':'green correct answer row observed'}
                     for w,m in [('cat','n.猫'),('dog','n.狗')]]
            (folder/'correct_word_answers.jsonl').write_text('\n'.join(json.dumps(x) for x in answers),encoding='utf-8')
            raw=question(1)[0];pending=(raw,listing());done=(raw,listing(2))
            dashboard={'width':1080,'height':2376,'elements':[element('今日任务',540,177),
                       element('单词',700,700),element('已完成3/3',700,750)]}
            frames=[pending,pending,pending,question(1),question(1),question(1,typed='cat'),
                    question(2,clue='n.狗'),question(2,clue='n.狗',typed='dog'),
                    (b'complete1',completed()),(b'complete2',completed()),done,done,done,(b'dashboard',dashboard)]
            # Distinguish equal PNG bytes in the fake capture by per-read handles.
            handles=[f'frame{i}'.encode() for i in range(len(frames))]
            lookup=dict(zip(handles,frames));adb=Mock();adb.log=RunLog(folder);adb.selector.return_value=('-t','1')
            attrs={'ROOT':root,'Adb':Mock(return_value=adb),'find_adb':Mock(return_value='adb'),
                   'parse_devices':Mock(return_value=[]),'load_engine':Mock(return_value=SimpleNamespace()),
                   'fresh_capture':Mock(side_effect=handles),'observe':Mock(side_effect=lambda key,*a,**kw:lookup[key][1]),
                   'require_wechat':Mock()}
            for key,value in attrs.items():stack.enter_context(patch.object(chain,key,value))
            # Pixel validation always sees the actual synthetic blank-slot PNG.
            from ocr_view import decode_image
            stack.enter_context(patch('word_navigation.decode_image',side_effect=lambda key:decode_image(lookup[key][0])))
            stack.enter_context(patch('spelling_batch.decode_image',side_effect=lambda key:decode_image(lookup[key][0])))
            stack.enter_context(patch('daily_spell.decode_image',side_effect=lambda key:decode_image(lookup[key][0])))
            stack.enter_context(patch('daily_spell.time.sleep'))
            api=stack.enter_context(patch('spelling_suggest.call_deepseek'))
            child=stack.enter_context(patch('daily_word_tasks.run_card_worker'))
            chain.main(['--max-cards','3'])
            report=json.loads((folder/'word_task_chain_result.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'],'word_tasks_completed_returned_to_dashboard')
            self.assertTrue(report['spelling_completed']);self.assertEqual(report['word_progress'],[3,3])
            api.assert_not_called();child.assert_not_called()
            self.assertEqual(adb.run.call_count,7)  # Start, two words+confirms, finish, dashboard.

    def test_two_words_share_runtime_and_use_five_reads_without_any_child_or_api(self):
        with TemporaryDirectory() as tmp,redirect_stdout(StringIO()):
            root=Path(tmp);folder=root/'runs/session';folder.mkdir(parents=True)
            (folder/'authorization.json').write_text('{"status":"active","automatic_answers":true}',encoding='utf-8')
            records=[{'word':w,'definition':[m],'feedback':'green correct answer row observed'}
                     for w,m in [('cat','n.猫'),('dog','n.狗')]]
            (folder/'correct_word_answers.jsonl').write_text('\n'.join(json.dumps(x) for x in records),encoding='utf-8')
            frames=[question(1),question(1,typed='cat'),question(2,clue='n.狗'),
                    question(2,clue='n.狗',typed='dog'),(b'completion-image',completed())]
            read=Mock(side_effect=frames);adb=Mock();adb.log=RunLog(folder)
            runtime=SpellingRuntime(adb,('-t','1'),read,Mock())
            with patch('daily_spell.time.sleep'),patch('spelling_suggest.call_deepseek') as api,patch('subprocess.Popen') as child:
                _,after,state=run_batch(runtime,folder,root=root,expected_header='四级写作词汇（新）',expected_total=2)
            self.assertEqual(read.call_count,5);self.assertEqual(adb.run.call_count,4)
            api.assert_not_called();child.assert_not_called()
            self.assertEqual(state['progress'],'2/2');self.assertEqual(after,completed())
            answers=[json.loads(x) for x in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines()
                     if json.loads(x)['event']=='daily_spelling_answer']
            self.assertEqual([x['word'] for x in answers],['cat','dog'])

    def test_old_frame_is_recaptured_and_a_changed_question_receives_no_input(self):
        clock=Mock(return_value=0);adb=Mock()
        read=Mock(side_effect=[question(1),question(2)])
        runtime=SpellingRuntime(adb,('-t','1'),read,Mock(),clock=clock)
        runtime.read();clock.return_value=3
        with self.assertRaisesRegex(RuntimeError,'进度变化'):
            runtime.answer('cat','n.猫','1/2')
        self.assertEqual(read.call_count,2);adb.run.assert_not_called()

    def test_wrong_course_after_typing_never_gets_a_confirm(self):
        with TemporaryDirectory() as tmp,patch('daily_spell.time.sleep'):
            adb=Mock();adb.log=RunLog(Path(tmp))
            read=Mock(side_effect=[question(1),question(1,typed='cat',course='另一英语课程')])
            runtime=SpellingRuntime(adb,('-t','1'),read,Mock())
            with self.assertRaisesRegex(RuntimeError,'Question changed'):runtime.answer('cat','n.猫','1/2')
            self.assertEqual(adb.run.call_count,1)

    def test_accepted_spelling_logs_supply_words_but_unsubmitted_suggestions_do_not(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'runs/session';folder.mkdir(parents=True)
            accepted={'event':'daily_spelling_answer','word':'cat','clue':['n.猫'],
                      'progress':'1/2','api_calls':0,'source':'reviewed_OCR_answer'}
            items=[accepted,dict(accepted,event='automatic_daily_answer',word='dog'),
                   dict(accepted,word='bad;command'),dict(accepted,word='pig',progress='3/2')]
            (folder/'events.jsonl').write_text('\n'.join(json.dumps(x) for x in items),encoding='utf-8')
            self.assertEqual(observed_bank(root),{'猫':{'cat'}})


class SpellingBudgetTests(unittest.TestCase):
    state={'header':'syntheticcourse','clue':'猫','raw_clue':'n.猫','progress':'1/25','length':3}
    def answer(self,**kw):
        return dict({'question_id':question_id(self.state),'action':'answer','word':'cat','confidence':.99,'reason':'Chinese meaning matches'},**kw)

    def test_payload_contains_task_text_only_and_response_never_contains_executable_actions(self):
        request=build_spelling_request(dict(self.state,api_key='SECRET',serial='PRIVATE'))
        body=json.dumps(request)
        self.assertNotIn('SECRET',body);self.assertNotIn('PRIVATE',body)
        self.assertEqual(validate_spelling_response(self.state,self.answer()),'cat')
        for update in ({'word':'cat;reboot'},{'word':'doggy'},{'confidence':.5},
                       {'question_id':'another question'},{'confidence':float('nan')},{'x':100}):
            with self.subTest(update=update),self.assertRaises(RuntimeError):
                validate_spelling_response(self.state,self.answer(**update))

    def test_no_budget_or_ended_authorization_never_calls_model(self):
        with TemporaryDirectory() as tmp,patch('spelling_suggest.call_deepseek') as api:
            folder=Path(tmp)
            for auth in ({'status':'active','automatic_answers':True},
                         {'status':'active','automatic_answers':True,'used_generation_calls':2,'max_new_deepseek_generation_calls':2},
                         {'status':'completed','automatic_answers':True,'max_new_deepseek_generation_calls':100}):
                (folder/'authorization.json').write_text(json.dumps(auth),encoding='utf-8')
                with self.assertRaises(RuntimeError):suggest_spelling(folder,self.state,Mock())
            api.assert_not_called()

    def test_one_request_consumes_existing_quota_and_same_question_reuses_validated_proposal(self):
        with TemporaryDirectory() as tmp,patch('spelling_suggest.load_api_key',return_value='test-local-key'),patch('spelling_suggest.call_deepseek',return_value=(self.answer(),{})) as api:
            folder=Path(tmp);auth={'status':'active','automatic_answers':True,'used_generation_calls':4,'max_new_deepseek_generation_calls':5}
            (folder/'authorization.json').write_text(json.dumps(auth),encoding='utf-8')
            self.assertEqual(suggest_spelling(folder,self.state,Mock())[0],'cat')
            self.assertEqual(suggest_spelling(folder,self.state,Mock())[0],'cat')
            self.assertEqual(api.call_count,1)
            current=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
            self.assertEqual((current['used_generation_calls'],current['max_new_deepseek_generation_calls']),(5,5))
            self.assertNotIn('test-local-key',''.join(p.read_text(encoding='utf-8') for p in folder.glob('*.json*')))

    def test_failed_request_is_counted_once_without_retry(self):
        with TemporaryDirectory() as tmp,patch('spelling_suggest.load_api_key',return_value='test-key'),patch('spelling_suggest.call_deepseek',side_effect=RuntimeError('network timeout')) as api:
            folder=Path(tmp);(folder/'authorization.json').write_text(json.dumps({'status':'active','automatic_answers':True,'max_new_deepseek_generation_calls':1}),encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'timeout'):suggest_spelling(folder,self.state,Mock())
            self.assertEqual(api.call_count,1)
            self.assertEqual(json.loads((folder/'authorization.json').read_text(encoding='utf-8'))['used_generation_calls'],1)


if __name__=='__main__':unittest.main()
