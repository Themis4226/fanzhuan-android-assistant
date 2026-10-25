"""Daily orchestration regressions: no phone, network, API or real task data."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

import daily_navigation as nav
from daily_tasks import DailyCoordinator, bind_choice_header
from daily_choice_pages import study_video_page


def el(text,x,y):
    return {'text':text,'center':[x,y],'bounds':[x-20,y-15,x+20,y+15],'confidence':.99}


def view(title, elements=(), width=1080, height=2400):
    return {'width':width,'height':height,'elements':[el(title,width//2,round(height*.07)),*elements]}


def dash(done=None, totals=None):
    done=done or {'单词':2,'语法':1,'阅读':0,'听力':0}
    totals=totals or {'单词':2,'语法':2,'阅读':1,'听力':1}
    elements=[]
    for i,k in enumerate(nav.CATEGORIES):
        x=180+(i%3)*330;y=700+(i//3)*430
        elements.extend([el(k,x,y),el(f'已完成{done.get(k,0)}/{totals.get(k,0)}',x,y+50)])
    n,total=sum(done.values()),sum(totals.values())
    elements.extend([el(f'{n}/{total}',915,370),el(f'{round(n/total*100)}%',610,297)])
    return view('今日任务',elements)


TITLES={'语法':('形容词修…','中级语法','形容词修饰名词',6),
        '阅读':('卡包01','中级阅读A','中级阅读A卡包01',8),
        '听力':('2013年','中级听力','2013年长对话（1-2）',8)}


def menu(category,done=False,width=1080,height=2400):
    title,course,_,pages=TITLES[category]
    image=Image.new('RGB',(width,height),'white');draw=ImageDraw.Draw(image)
    x,y,w,h=round(width*.04),round(height*.17),round(width*.22),round(width*.24)
    draw.rounded_rectangle((x,y,x+w,y+h),radius=8,fill=(248,248,248))
    if done:
        draw.polygon([(x+w-round(w*.48),y),(x+w,y),(x+w,y+round(h*.48))],fill=(25,195,177))
    elements=[el(course,width//3,round(height*.14)),el(title,x+w//2,y+round(h*.35)),
              el(str(pages)+'篇',x+w//2,y+round(h*.7))]
    data=BytesIO();image.save(data,format='PNG')
    return data.getvalue(),view(category+'任务',elements,width,height)


class FakePhone:
    def __init__(self, done=None, fail=False):
        self.done=done or {'单词':2,'语法':1,'阅读':0,'听力':0}
        self.state='dashboard';self.category=None;self.raw=None;self.taps=[];self.workers=[];self.fail=fail

    def read(self):
        if self.state=='dashboard':
            self.raw=b'not-used-at-dashboard';return dash(self.done)
        if self.state=='menu':
            self.raw,v=menu(self.category,self.done[self.category]=={'语法':2,'阅读':1,'听力':1}[self.category]);return v
        title,course,full,pages=TITLES[self.category]
        self.raw=b'not-used-at-details'
        return view(full,[el(f'1/{pages}',770,2320)] if self.state=='active'
                    else [el('COMPLETE',540,1100),el('太棒了，卡包已完成~',540,1400),el('完成',540,2120)])

    def tap(self,center):
        self.taps.append(center)
        if self.state=='dashboard':
            self.category=next(k for k,v in nav.dashboard(self.read()).items() if v['center']==center)
            self.state='menu'
        elif self.state=='menu':
            self.state='dashboard' if center[0]<70 else 'active'
        elif self.state=='completion':
            self.done[self.category]+=1;self.state='menu'
        else:
            raise AssertionError('Unexpected tap')

    def worker(self,args):
        self.workers.append(args)
        if self.fail:return 1,'TEST_CHILD_PAUSED'
        if args[0]=='daily_word_tasks.py':self.done['单词']=2;self.state='dashboard'
        else:self.state='completion'
        return 0,''


class DailyNavigationTests(unittest.TestCase):
    def test_counts_bound_to_categories_and_complete_categories_skipped(self):
        progress=nav.dashboard(dash())
        self.assertEqual([progress['单词']['done'],progress['单词']['total']],[2,2])
        self.assertEqual(nav.next_category(progress),'语法')

    def test_unknown_positive_category_blocks_before_tap(self):
        progress=nav.dashboard(dash(totals={'单词':2,'语法':2,'阅读':1,'听力':1,'写作':1}))
        with self.assertRaisesRegex(RuntimeError,'尚未适配'):nav.next_category(progress)

    def test_duplicate_counts_or_wrong_aggregate_rejected(self):
        for mutation in ('duplicate','aggregate'):
            v=dash()
            if mutation=='duplicate':v['elements'].append(el('已完成0/1',180,750))
            else:next(e for e in v['elements'] if e['text']=='3/6')['text']='4/6'
            with self.subTest(case=mutation),self.assertRaises(RuntimeError):nav.dashboard(v)

    def test_gray_tiles_and_completion_ribbons_at_two_sizes(self):
        for category in TITLES:
            for size in ((1080,2400),(720,1600)):
                for done in (False,True):
                    raw,v=menu(category,done,*size)
                    tiles=nav.course_tiles(raw,v,category)
                    self.assertEqual(len(tiles),1);self.assertEqual(tiles[0]['completed'],done)

    def test_entry_requires_course_title_and_total_pages(self):
        raw,v=menu('阅读');target=nav.course_tiles(raw,v,'阅读')[0]
        self.assertTrue(nav.entered_card(view('中级阅读A卡包01',[el('1/8',770,2320)]),target))
        self.assertFalse(nav.entered_card(view('其他阅读卡包01',[el('1/8',770,2320)]),target))
        self.assertFalse(nav.entered_card(view('中级阅读A卡包01',[el('1/9',770,2320)]),target))
        self.assertFalse(nav.entered_card(view('中级阅读A卡包02',[el('1/8',770,2320)]),target))

    def test_no_progress_and_other_category_change_are_not_completion(self):
        before=nav.dashboard(dash())
        with self.assertRaisesRegex(RuntimeError,'未证实'):nav.verify_progress(before,before,'语法')
        after=nav.dashboard(dash({'单词':2,'语法':2,'阅读':1,'听力':0}))
        with self.assertRaisesRegex(RuntimeError,'意外变化'):nav.verify_progress(before,after,'语法')

    def test_video_skip_needs_all_observed_markers(self):
        title='形容词修饰名词'
        lesson=view(title,[el('翻转外语',1000,345),el('02:53',960,880),el(title,380,1120)])
        self.assertTrue(study_video_page(lesson,title))
        for text in ('翻转外语','02:53'):
            incomplete=dict(lesson,elements=[e for e in lesson['elements'] if e['text']!=text])
            self.assertFalse(study_video_page(incomplete,title))

    def test_completion_modal_close_is_once_then_counts_verified(self):
        modal=view('今日任务',[el('100%',610,297),el('完成今日所有任务！',540,1050)])
        final=dash({'单词':2,'语法':2,'阅读':1,'听力':1});tap=Mock()
        with patch.object(nav,'wait_for',side_effect=lambda read,predicate,reason:read()):
            result=nav.dismiss_all_complete(modal,Mock(side_effect=[modal,final]),tap,Mock())
        tap.assert_called_once_with([919,327]);self.assertEqual(nav.next_category(nav.dashboard(result)),None)


class DailyCoordinatorTests(unittest.TestCase):
    def coordinator(self,phone):
        self.reports=[];self.authorize=Mock()
        return DailyCoordinator(phone.read,lambda:phone.raw,phone.tap,phone.worker,self.authorize,Mock(),
                                lambda r:self.reports.append(json.loads(json.dumps(r))),skip_study=True)

    def test_grammar_reading_listening_chain_without_human_or_model(self):
        phone=FakePhone();coordinator=self.coordinator(phone)
        with patch('word_navigation.time.sleep'),redirect_stdout(StringIO()):
            coordinator.run()
        self.assertEqual([v['category'] for v in coordinator.report['completed']],['语法','阅读','听力'])
        self.assertEqual(coordinator.report['status'],'all_daily_tasks_verified')
        self.assertEqual(len(phone.workers),3)
        self.assertEqual([a.args[0] for a in self.authorize.call_args_list],['语法','阅读'])
        self.assertIn('--skip-study',phone.workers[0]);self.assertNotIn('--skip-study',phone.workers[2])

    def test_word_worker_returns_dashboard_before_cross_category_dispatch(self):
        phone=FakePhone({'单词':1,'语法':2,'阅读':1,'听力':1})
        original=phone.read
        def read():
            if phone.category=='单词' and phone.state=='menu':
                phone.raw=b'word-menu';return view('单词任务')
            return original()
        phone.read=read;coordinator=self.coordinator(phone)
        with patch('word_navigation.time.sleep'),redirect_stdout(StringIO()):coordinator.run()
        self.assertEqual(phone.workers[0][0],'daily_word_tasks.py')
        self.assertEqual(coordinator.report['status'],'all_daily_tasks_verified')

    def test_child_failure_preserves_page_without_advancing_other_subject(self):
        phone=FakePhone(fail=True);coordinator=self.coordinator(phone)
        with patch('word_navigation.time.sleep'),redirect_stdout(StringIO()),self.assertRaisesRegex(RuntimeError,'TEST_CHILD_PAUSED'):
            coordinator.run()
        self.assertEqual(len(phone.workers),1);self.assertEqual(phone.state,'active')
        self.assertEqual(coordinator.report['status'],'paused')

    def test_unknown_start_page_is_not_mutated(self):
        phone=FakePhone();phone.state='active';phone.category='听力';coordinator=self.coordinator(phone)
        with self.assertRaises(RuntimeError):coordinator.run()
        self.assertEqual(phone.taps,[]);self.assertEqual(phone.workers,[])

    def test_task_limit_pauses_after_verified_completed_card(self):
        phone=FakePhone();coordinator=self.coordinator(phone)
        with patch('word_navigation.time.sleep'),redirect_stdout(StringIO()),self.assertRaisesRegex(RuntimeError,'最多任务数'):
            coordinator.run(max_tasks=1)
        self.assertEqual(phone.state,'dashboard');self.assertEqual(len(phone.workers),1)

    def test_choice_scope_binding_preserves_budget_and_rejects_ungranted_category(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'auth.json'
            auth={'status':'active','automatic_answers':True,'arbitrary_choice_categories':['语法'],
                  'used_generation_calls':24,'max_new_deepseek_generation_calls':113}
            path.write_text(json.dumps(auth),encoding='utf-8')
            bind_choice_header(path,'语法','形容词修饰名词')
            changed=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(changed['used_generation_calls'],24);self.assertEqual(changed['max_new_deepseek_generation_calls'],113)
            self.assertEqual(changed['arbitrary_choice_headers'],['形容词修饰名词'])
            with self.assertRaises(RuntimeError):bind_choice_header(path,'阅读','Other')


if __name__=='__main__':unittest.main()
