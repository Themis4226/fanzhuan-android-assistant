from contextlib import ExitStack,redirect_stdout
from io import BytesIO,StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import sys
from unittest.mock import Mock,patch

from PIL import Image,ImageDraw
import daily_words
import daily_word_tasks
import word_navigation as nav


def element(text,x,y,confidence=.99):
    return {'text':text,'center':[x,y],'bounds':[x-20,y-15,x+20,y+15],
            'confidence':confidence,'id':text}


def completion():
    return {'width':1080,'height':2400,'elements':[
        element('新时代大学进阶英语1卡包6',540,165),element('COMPLETE',540,830),
        element('恭喜你完成卡包学习!',540,960),element('本次学习',315,1140),
        element('完成',1030,290),element('完成',540,2130)]}


def active(card=7):
    return {'width':1080,'height':2400,'elements':[
        element('新时代大学进阶英语1卡包'+str(card),540,165),
        element('学习0/25',80,290),element('cat',540,1000)]}


def word_list(done=(6,),width=1080,height=2400,badge_ocr=True):
    image=Image.new('RGB',(width,height),'white');draw=ImageDraw.Draw(image)
    v={'width':width,'height':height,'elements':[element('单词任务',width//2,round(height*.07)),
        element('新时代大学进阶英语1',width//3,round(height*.27))]}
    for i,card in enumerate((6,7)):
        x=round(width*(.04+i*.25));y=round(height*.3);w=round(width*.215);h=round(width*.235)
        draw.rounded_rectangle((x,y,x+w,y+h),radius=10,fill=(248,248,248))
        if card in done:
            draw.polygon([(x+w-110*width//1080,y),(x+w,y),(x+w,y+110*width//1080)],fill=(25,195,177))
            if badge_ocr:v['elements'].append(element('已完成',x+w-35*width//1080,y+35*width//1080))
        v['elements'].extend([element('卡包'+str(card),x+w//2,y+int(h*.33)),
                              element('25词',x+w//2,y+int(h*.70))])
    f=BytesIO();image.save(f,format='PNG');return f.getvalue(),v


def dashboard(n=2,total=2):
    return {'width':1080,'height':2400,'elements':[element('今日任务',540,165),
        element('单词',700,700),element(f'已完成{n}/{total}',700,750),
        element('语法',530,1140),element('已完成1/2',530,1190)]}


class CompletionReturnTests(unittest.TestCase):
    def test_bottom_complete_is_used_and_word_list_verified(self):
        view=completion();listing=word_list()[1];read=Mock(side_effect=[view,listing]);tap=Mock()
        with patch.object(nav.time,'sleep'):after=nav.return_to_word_list(view,read,tap,Mock())
        tap.assert_called_once_with([540,2130]);self.assertTrue(nav.is_word_list(after))

    def test_changed_or_ambiguous_completion_never_taps(self):
        for current in (active(),dict(completion(),elements=completion()['elements']+[element('完成',550,2100)])):
            tap=Mock()
            with self.subTest(view=current),self.assertRaises(RuntimeError):
                nav.return_to_word_list(completion(),Mock(return_value=current),tap,Mock())
            tap.assert_not_called()

    def test_failed_return_reads_again_without_replaying_input(self):
        tap=Mock()
        with patch.object(nav.time,'sleep'),self.assertRaisesRegex(RuntimeError,'未回到'):
            nav.return_to_word_list(completion(),Mock(return_value=completion()),tap,Mock())
        self.assertEqual(tap.call_count,1)


class PendingCardTests(unittest.TestCase):
    def test_completed_ribbon_is_detected_with_missing_ocr_at_two_resolutions(self):
        for size in ((1080,2400),(720,1600)):
            raw,v=word_list(width=size[0],height=size[1],badge_ocr=False)
            tiles=nav.task_tiles(raw,v)
            self.assertEqual([(t['card_id'],t['completed']) for t in tiles],[(6,True),(7,False)])

    def test_complete_text_without_expected_ribbon_is_ambiguous(self):
        raw,v=word_list(done=())
        v['elements'].append(element('已完成',230,755))
        with self.assertRaisesRegex(RuntimeError,'角标'):nav.task_tiles(raw,v)

    def test_next_pending_card_skips_completed_and_checks_entered_header(self):
        raw,v=word_list();tap=Mock()
        with patch.object(nav.time,'sleep'):
            number,_=nav.open_pending_card(Mock(side_effect=[v,active(7)]),lambda:raw,tap,Mock())
        self.assertEqual(number,7)
        self.assertEqual(tap.call_args.args[0],nav.task_tiles(raw,v)[1]['center'])

    def test_reappearing_attempted_card_is_not_replayed(self):
        raw,v=word_list();tap=Mock()
        with self.assertRaisesRegex(RuntimeError,'重复刷题'):
            nav.open_pending_card(Mock(return_value=v),lambda:raw,tap,Mock(),attempted=[7])
        tap.assert_not_called()

    def test_capsule_obscured_number_requires_matching_course_and_new_learning_page(self):
        raw,v=word_list();inside=active(7);inside['elements'][0]['text']='新时代大学进阶英语1卡包.'
        self.assertIsNone(nav.active_card_id(inside))
        with patch.object(nav.time,'sleep'):
            number,_=nav.open_pending_card(Mock(side_effect=[v,inside]),lambda:raw,Mock(),Mock())
        self.assertEqual(number,7)
        inside['elements'][0]['text']='其他英语课程卡包.'
        with patch.object(nav.time,'sleep'),self.assertRaisesRegex(RuntimeError,'预期'):
            nav.open_pending_card(Mock(side_effect=[v]+[inside]*4),lambda:raw,Mock(),Mock())

    def test_no_pending_card_has_no_input(self):
        raw,v=word_list(done=(6,7));tap=Mock()
        number,_=nav.open_pending_card(Mock(return_value=v),lambda:raw,tap,Mock())
        self.assertIsNone(number);tap.assert_not_called()

    def test_capsule_partial_number_is_not_treated_as_a_different_card(self):
        raw,v=word_list(done=(6,))
        next(e for e in v['elements'] if e['text']=='卡包7')['text']='卡包14'
        inside=active(14);inside['elements'][0]['text']='新时代大学进阶英语1卡包1'
        inside['elements'][0]['bounds']=[220,148,876,207]
        self.assertIsNone(nav.active_card_id(inside))
        with patch.object(nav.time,'sleep'):
            number,_=nav.open_pending_card(Mock(side_effect=[v,inside]),lambda:raw,Mock(),Mock())
        self.assertEqual(number,14)

    def test_clear_wrong_number_or_inconsistent_clipped_prefix_is_rejected(self):
        raw,v=word_list(done=(6,))
        next(e for e in v['elements'] if e['text']=='卡包7')['text']='卡包14'
        for bounds,text in (([400,148,600,207],'新时代大学进阶英语1卡包1'),
                            ([220,148,876,207],'新时代大学进阶英语1卡包2')):
            inside=active(1);inside['elements'][0].update(text=text,bounds=bounds)
            with self.subTest(text=text,bounds=bounds),patch.object(nav.time,'sleep'),self.assertRaisesRegex(RuntimeError,'预期'):
                nav.open_pending_card(Mock(side_effect=[v]+[inside]*4),lambda:raw,Mock(),Mock())

    def test_card_entry_must_match_the_tile_word_count(self):
        raw,v=word_list();inside=active(7);inside['elements'][1]['text']='学习0/30'
        with patch.object(nav.time,'sleep'),self.assertRaisesRegex(RuntimeError,'预期'):
            nav.open_pending_card(Mock(side_effect=[v]+[inside]*4),lambda:raw,Mock(),Mock())

    def test_dashboard_progress_belongs_to_word_category(self):
        self.assertEqual(nav.dashboard_word_progress(dashboard(1,2)),[1,2])


class WorkerWiringTests(unittest.TestCase):
    def run_worker(self,module,frames,views,argv,child_code=0):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);folder=root/'runs/session';folder.mkdir(parents=True)
            (root/'runs/active_daily_session.txt').write_text(str(folder),encoding='utf-8')
            (folder/'authorization.json').write_text(json.dumps({'automatic_answers':True,'status':'active',
                'used_generation_calls':0,'max_new_deepseek_generation_calls':13}),encoding='utf-8')
            adb=Mock();adb.selector.return_value=('-t','1')
            lookup=dict(zip(frames,views))
            attrs={'ROOT':root,'Adb':Mock(return_value=adb),'find_adb':Mock(return_value='adb'),
                   'parse_devices':Mock(return_value=[]),'load_engine':Mock(return_value=SimpleNamespace()),
                   'fresh_capture':Mock(side_effect=frames),'observe':Mock(side_effect=lambda data,*a,**kw:lookup[data]),'require_wechat':Mock()}
            for name,value in attrs.items():stack.enter_context(patch.object(module,name,value))
            stack.enter_context(patch.object(nav.time,'sleep'))
            stack.enter_context(redirect_stdout(StringIO()))
            if module is daily_words:stack.enter_context(patch.object(daily_words,'call_deepseek',side_effect=AssertionError('No API')))
            child=stack.enter_context(patch.object(daily_word_tasks,'run_card_worker',return_value=(child_code,'test worker error')))
            module.main(argv)
            report=json.loads((folder/'word_task_chain_result.json').read_text(encoding='utf-8')) if module is daily_word_tasks else None
            return adb,report,child.call_count

    def test_existing_completion_returns_without_any_swipe_or_model(self):
        raw,v=word_list();c=completion()
        adb,_,_=self.run_worker(daily_words,[b'complete-a',b'complete-b',raw],[c,c,v],['--count','1'])
        self.assertEqual(adb.run.call_count,1);self.assertIn('tap',adb.run.call_args.args)

    def test_completion_on_last_allowed_step_still_returns(self):
        raw,v=word_list();c=completion()
        adb,_,_=self.run_worker(daily_words,[b'front',b'complete-a',b'complete-b',raw],
            [active(6),c,c,v],['--skip-review','--count','1'])
        self.assertEqual(adb.run.call_count,2)
        self.assertIn('swipe',adb.run.call_args_list[0].args);self.assertIn('tap',adb.run.call_args_list[1].args)

    def test_navigation_only_returns_then_opens_next_without_answer_worker(self):
        raw,v=word_list();c=completion();a=active(7)
        adb,report,children=self.run_worker(daily_word_tasks,
            [b'c1',b'c2',raw,raw,b'active7'],[c,c,v,v,a],['--navigation-only'])
        self.assertEqual(report['opened_cards'],[7]);self.assertEqual(children,0)
        self.assertEqual(report['status'],'next_pending_card_opened_no_answers')
        self.assertEqual(adb.run.call_count,2)

    def test_two_workers_chain_and_return_to_dashboard_when_word_count_complete(self):
        raw,v=word_list();allraw,allv=word_list(done=(6,7));a6=active(6);a7=active(7);dash=dashboard()
        frames=[b'a6',raw,raw,b'a7',allraw,allraw,allraw,b'dash']
        views=[a6,v,v,a7,allv,allv,allv,dash]
        _,report,children=self.run_worker(daily_word_tasks,frames,views,['--max-cards','3'])
        self.assertEqual(children,2);self.assertEqual(report['completed_cards'],[6,7])
        self.assertEqual(report['word_progress'],[2,2])
        self.assertEqual(report['status'],'word_tasks_completed_returned_to_dashboard')

    def test_failed_answer_worker_stops_without_navigation_retry(self):
        with self.assertRaisesRegex(RuntimeError,'单词脚本已暂停'):
            self.run_worker(daily_word_tasks,[b'a7'],[active(7)],[],child_code=1)

    def test_nested_worker_unicode_output_and_actual_failure_are_forwarded(self):
        output=StringIO()
        with redirect_stdout(output):
            code,last=daily_word_tasks.run_card_worker([sys.executable,'-X','utf8','-u','-c',
                "print('子进程中文进度',flush=True);raise RuntimeError('TEST_BUDGET_EXHAUSTED')"],Path.cwd())
        self.assertNotEqual(code,0)
        self.assertIn('子进程中文进度',output.getvalue())
        self.assertIn('TEST_BUDGET_EXHAUSTED',last)

    def test_resume_checkpoint_requires_same_question_and_course(self):
        view=active(7);view['elements'][0]['text']='新时代大学进阶英语1卡包.'
        prior={'status':'paused','resume_card_id':7,'resume_signature':daily_words.question_signature(view),
               'resume_header':daily_word_tasks.card_header(view)}
        self.assertTrue(daily_word_tasks.resume_matches(prior,view))
        view['elements'][1]['text']='学习1/25'
        self.assertFalse(daily_word_tasks.resume_matches(prior,view))

    def test_resume_allows_capsule_to_hide_only_the_last_digit_of_same_card(self):
        view=active(15);view['elements'][0]['text']='新时代大学进阶英语1卡包15'
        prior={'status':'paused','resume_card_id':15,'resume_signature':daily_words.question_signature(view),
               'resume_header':daily_word_tasks.card_header(view)}
        view['elements'][0].update(text='新时代大学进阶英语1卡包1',bounds=[220,148,876,207])
        self.assertTrue(daily_word_tasks.resume_matches(prior,view))
        view['elements'][0]['bounds']=[220,148,650,207]
        self.assertFalse(daily_word_tasks.resume_matches(prior,view))
        view['elements'][0].update(text='新时代大学进阶英语1卡包2',bounds=[220,148,876,207])
        self.assertFalse(daily_word_tasks.resume_matches(prior,view))
        view['elements'][1]['text']='学习0/25';view['elements'][0]['text']='另一本英语课卡包.'
        self.assertFalse(daily_word_tasks.resume_matches(prior,view))


if __name__=='__main__':unittest.main()
