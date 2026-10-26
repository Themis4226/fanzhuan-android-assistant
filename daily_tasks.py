"""Bounded daily-task coordinator, driven by verified dashboard progress."""
import argparse
from datetime import date
import json
import re
from pathlib import Path
import sys

from assistant_step import fresh_capture, require_wechat
from daily_privacy import notification_free_frame
from daily_word_tasks import run_card_worker, resume_matches
from spelling_navigation import spelling_page
from word_navigation import recognized_word_start,is_completion as word_completion,return_active_to_list,return_to_word_list
from fast_observer import ExactFrameObserver
from ocr_view import load_engine, observe
from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices
import daily_navigation as nav


def load_entry_headers(root,folder):
    """Reuse verified UI vocabulary across phones, never a phone's progress."""
    catalog=root/'course_catalog.json'
    entries={}
    if catalog.is_file():
        data=json.loads(catalog.read_text(encoding='utf-8'))
        if data.get('schema_version')!=1 or not isinstance(data.get('entries'),dict):
            raise RuntimeError('课程入口目录格式不合法。')
        entries.update(data['entries'])
    path=folder/'reviewed_course_entries.json'
    if path.is_file():entries.update(json.loads(path.read_text(encoding='utf-8')))
    return entries


def bind_choice_header(auth_path, category, title):
    """Narrow a prior category-level opt-in to the just-observed course title."""
    auth = json.loads(auth_path.read_text(encoding='utf-8'))
    if auth.get('status') != 'active' or not auth.get('automatic_answers'):
        raise RuntimeError('本次会话自动答题授权已结束。')
    allowed = auth.get('arbitrary_choice_headers', [])
    if title in allowed:
        return
    if category not in {'语法', '阅读'} or category not in auth.get('arbitrary_choice_categories', []):
        raise RuntimeError('尚未授权此语法/阅读课程直接选择选项：' + title)
    auth['arbitrary_choice_headers'] = allowed + [title]
    auth_path.write_text(json.dumps(auth, ensure_ascii=False, indent=2), encoding='utf-8')


def listening_resume_target(checkpoint,view,entry_headers):
    """Resume only a same-day, inventoried listening card with a known title."""
    if (checkpoint.get('status') not in {'paused','running'}
            or checkpoint.get('date')!=date.today().isoformat()
            or checkpoint.get('active_category')!='听力'):
        return None
    target=checkpoint.get('active_target')
    if not isinstance(target,dict) or target.get('category')!='听力':return None
    entry=entry_headers.get(target.get('key'))
    if (not isinstance(entry,dict) or entry.get('source') not in {'observed_ui','assistant_review'}
            or entry.get('category')!='听力' or entry.get('pages')!=target.get('pages')
            or not entry.get('header') or not nav.entered_card(view,target,entry['header'])):
        return None
    before=checkpoint.get('progress')
    if not isinstance(before,dict) or '听力' not in before:return None
    from daily_listening import intro_confirm
    # A matching title/counter alone must not turn an unrelated page into audio.
    if intro_confirm(view) is None and not any(re.fullmatch(r'(?:已播放第?\d+遍|第\d+遍播放中)',e['text'])
                                             for e in view['elements']):
        return None
    return target,entry['header'],before


class DailyCoordinator:
    def __init__(self, read, get_raw, tap, worker, authorize_choice, log, save, *, skip_study=False, resume_word=None,categories=nav.SUPPORTED,entry_headers=None,resume_checkpoint=None):
        self.read, self.get_raw, self.tap = read, get_raw, tap
        self.worker, self.authorize_choice, self.log, self.save = worker, authorize_choice, log, save
        self.skip_study = skip_study
        self.resume_word = resume_word
        self.categories=tuple(categories)
        self.entry_headers=entry_headers or {}
        self.resume_checkpoint=resume_checkpoint or {}
        self.report = {'status': 'running', 'date': date.today().isoformat(), 'completed': [],'categories':list(categories)}

    def record(self, **values):
        self.report.update(values); self.save(self.report)

    def call_worker(self, args):
        code, last_line = self.worker(args)
        if code:
            raise RuntimeError('科目脚本暂停：' + last_line + '；保留页面，不跳过或自动重试。')

    def run(self, max_tasks=20):
        self.record()
        try:
            view = self.read()
            resume=listening_resume_target(self.resume_checkpoint,view,self.entry_headers) if '听力' in self.categories else None
            if resume is not None:
                target,title,before=resume
                self.record(active_category='听力',active_target=target,progress=before)
                print('当前听力标题、页数与本次暂停入口一致，继续播放并处理已知说明弹窗。',flush=True)
                self.call_worker(['daily_listening.py','--header',title,'--count',str(target['pages'])])
                view=nav.finish_card(self.read(),'听力',title,self.read,self.tap,self.log)
                verified=[t for t in nav.course_tiles(self.get_raw(),view,'听力')
                          if t['key']==target['key'] and t['completed']]
                if len(verified)!=1:raise RuntimeError('恢复听力后列表未证实当前卡片完成。')
                view=nav.back_from_menu(view,'听力',self.read,self.tap,self.log)
                if nav.all_complete_modal(view):view=nav.dismiss_all_complete(view,self.read,self.tap,self.log)
                after=nav.dashboard(view);nav.verify_progress(before,after,'听力')
                self.report['completed'].append(dict(target,category='听力'))
                self.record(progress=after,active_target=None)
            word_page=recognized_word_start(view) or word_completion(view)
            if '单词' in self.categories and (word_page or spelling_page(view) or (self.resume_word is not None and self.resume_word(view))):
                self.record(active_category='单词')
                print('已识别单词任务页面，继续核对卡包或拼写；没有旧检查点时按当前列表重新确认入口。', flush=True)
                self.log.event('daily_start_word_page_recognized',has_matching_checkpoint=bool(self.resume_word and self.resume_word(view)),api_calls=0)
                self.call_worker(['daily_word_tasks.py', '--count', '50', '--max-cards', '10'])
                view = self.read()
            elif word_page:
                # Selected other subjects: navigate out of a confirmed word page
                # without answering it, then inventory the real dashboard.
                view=(return_to_word_list(view,self.read,self.tap,self.log) if word_completion(view)
                      else return_active_to_list(view,self.read,self.tap,self.log))
            # A confirmed list can return by the app's own top-left arrow.
            # Unknown details are left intact; launch daily mode at the dashboard.
            for category in nav.SUPPORTED:
                if nav.is_menu(view, category):
                    view = nav.back_from_menu(view, category, self.read, self.tap, self.log)
                    break
            if not nav.has_header(view,'今日任务'):
                raise RuntimeError('当前页面不是首页或可恢复的任务页；请在手机返回“今日任务”后点击开始。'
                                   '无需新建会话；现有进度和调用额度保留。')
            for step in range(max_tasks+1):
                if nav.all_complete_modal(view):
                    view = nav.dismiss_all_complete(view, self.read, self.tap, self.log)
                before = nav.dashboard(view)
                self.record(progress=before)
                category = nav.next_category(before,self.categories)
                if category is None:
                    pending=[k for k,v in before.items() if v['done']<v['total']]
                    if pending:
                        self.record(status='selected_categories_verified',active_target=None,remaining_categories=pending)
                        print('本次选择的科目已确认完成；仍待处理：'+'、'.join(pending),flush=True)
                        return view
                    # Completion must include the rate as well as every category count.
                    if not any(e['text'] == '100%' and e.get('confidence', 0) >= .9
                               and e['center'][1] < view['height']*.25 for e in view['elements']):
                        raise RuntimeError('各分类已满，但尚未确认首页100%完成率。')
                    self.record(status='all_daily_tasks_verified', active_target=None)
                    print('全部每日任务已由首页总计、分类计数和100%完成率确认。', flush=True)
                    return view
                if step == max_tasks:
                    raise RuntimeError('达到本次最多任务数，保留进度；可从今日任务首页继续。')
                print('剩余任务：'+ '，'.join(k+str(v['done'])+'/'+str(v['total']) for k,v in before.items()
                                           if v['done'] < v['total']), flush=True)
                self.record(active_category=category)
                # Re-read before input, including totals, so an old dashboard cannot steer a tap.
                current = self.read(); current_progress = nav.dashboard(current)
                if current_progress != before:
                    raise RuntimeError('首页在点击前已变化，请核对后重新运行。')
                self.tap(current_progress[category]['center'])
                view = nav.wait_for(self.read, lambda v: nav.is_menu(v, category), '未进入预期科目列表')
                target = None
                if category == '单词':
                    self.call_worker(['daily_word_tasks.py', '--count', '50', '--max-cards', '10'])
                    view = self.read()
                    if nav.all_complete_modal(view):
                        view = nav.dismiss_all_complete(view, self.read, self.tap, self.log)
                else:
                    tiles = nav.course_tiles(self.get_raw(), view, category)
                    pending = [t for t in tiles if not t['completed']]
                    if not pending:
                        raise RuntimeError('首页仍有未完成项，但当前列表没有可见待完成卡片；不盲目翻页。')
                    target = pending[0]
                    if target['key'] in [t['key'] for t in self.report['completed']]:
                        raise RuntimeError('已验证完成的任务再次显示待完成，拒绝重复执行。')
                    if target['pages'] > (30 if category == '听力' else 20):
                        raise RuntimeError('任务页数超出子脚本上限，保留现场。')
                    self.record(active_target=dict(target, category=category))
                    # Take another fresh list before tapping this exact tile.
                    current = self.read()
                    fresh = [t for t in nav.course_tiles(self.get_raw(), current, category) if t['key'] == target['key']]
                    if len(fresh) != 1 or fresh[0] != target:
                        raise RuntimeError('任务入口已变化，停止使用旧位置。')
                    self.tap(target['center'])
                    reviewed_entry=self.entry_headers.get(target['key'])
                    expected_header=None
                    if reviewed_entry is not None:
                        if (reviewed_entry.get('source') not in {'assistant_review','observed_ui'} or reviewed_entry.get('pages')!=target['pages']
                                or reviewed_entry.get('category')!=category or not reviewed_entry.get('header')):
                            raise RuntimeError('已核对的课程入口记录与当前卡片不符。')
                        expected_header=reviewed_entry['header']
                    view = nav.wait_for(self.read, lambda v: nav.entered_card(v, target,expected_header), '未进入与卡片标题及篇数一致的课程')
                    title = nav.header(view)['text']
                    print('开始'+category+'：'+title+'（'+str(target['pages'])+'页）', flush=True)
                    if category == '听力':
                        args = ['daily_listening.py', '--header', title, '--count', str(target['pages'])]
                    else:
                        self.authorize_choice(category, title)
                        args = ['daily_choice_pages.py', '--header', title, '--count', str(target['pages']), '--allow-info-and-scroll']
                        if self.skip_study:
                            args.append('--skip-study')
                    self.call_worker(args)
                    view = nav.finish_card(self.read(), category, title, self.read, self.tap, self.log)
                    verified = [t for t in nav.course_tiles(self.get_raw(), view, category)
                                if t['key'] == target['key'] and t['completed']]
                    if len(verified) != 1:
                        raise RuntimeError('科目列表未证实本卡片完成，不会继续下一项。')
                    view = nav.back_from_menu(view, category, self.read, self.tap, self.log)
                    if nav.all_complete_modal(view):
                        view = nav.dismiss_all_complete(view, self.read, self.tap, self.log)
                after = nav.dashboard(view)
                nav.verify_progress(before, after, category)
                if target is not None:
                    self.report['completed'].append(dict(target, category=category))
                self.log.event('daily_category_progress_verified', category=category,
                               before=before[category], after=after[category], api_calls=0)
                self.record(progress=after, active_target=None)
                print(category+'进度已确认：'+str(after[category]['done'])+'/'+str(after[category]['total']), flush=True)
        except Exception as error:
            self.record(status='paused', reason=str(error))
            raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--max-tasks', type=int, default=20)
    p.add_argument('--categories',nargs='+',choices=nav.SUPPORTED,default=list(nav.SUPPORTED),
                   help='Only process the explicitly selected categories; never claim skipped categories completed')
    a = p.parse_args(argv)
    if not 1 <= a.max_tasks <= 30:
        p.error('max-tasks must be 1..30')
    if len(set(a.categories))!=len(a.categories):p.error('categories must be unique')
    folder = Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    auth_path = folder/'authorization.json'
    auth = json.loads(auth_path.read_text(encoding='utf-8'))
    if (auth.get('status') != 'active' or not auth.get('automatic_answers')
            or not auth.get('automatic_daily_submissions')):
        raise RuntimeError('没有进行中的自动答题及每日提交授权。')
    log = RunLog(folder); adb = Adb(find_adb(None), 20, log)
    selector = adb.selector(adb.select_usb(parse_devices(adb.text('devices', '-l')), auth.get('serial')))
    engine = load_engine(); engine.use_cls = False
    observer = ExactFrameObserver(engine, observe, log=log)
    raw = None; latest = None

    def read():
        nonlocal raw, latest
        if json.loads(auth_path.read_text(encoding='utf-8')).get('status') != 'active':
            raise RuntimeError('本次会话已结束。')
        def capture():
            require_wechat(adb,selector)
            return fresh_capture(adb,selector)
        captured,latest=notification_free_frame(capture,lambda data:observer(data,live=True),log)
        raw = captured
        (folder/'latest.png').write_bytes(raw)
        (folder/'latest.json').write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding='utf-8')
        return latest

    def tap(center):
        if (latest is None or len(center) != 2 or any(type(c) is not int for c in center)
                or not 0 <= center[0] < latest['width'] or not 0 <= center[1] < latest['height']):
            raise RuntimeError('点击坐标不合法。')
        require_wechat(adb, selector)
        adb.run(*selector, 'shell', 'input', 'tap', *(str(c) for c in center))
        log.event('daily_coordinator_tap', center=center, api_calls=0)

    def worker(args):
        return run_card_worker([sys.executable, '-X', 'utf8', '-u', str(ROOT/args[0]), *args[1:]], ROOT)

    def save(report):
        (folder/'daily_task_chain_result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        # Keep the inventoried target when a later wrong-screen start replaces
        # the main report. No coordinates are used without a fresh page check.
        resume_path=folder/'daily_resume_checkpoint.json'
        if report.get('active_target') is not None:
            resume_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif report.get('completed') and resume_path.is_file():
            saved=json.loads(resume_path.read_text(encoding='utf-8'))
            target=saved.get('active_target') or {}
            if any(t.get('key')==target.get('key') for t in report['completed']):
                saved['status']='resolved'
                resume_path.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')

    checkpoint_path=folder/'word_task_chain_result.json'
    checkpoint=json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.is_file() else {}
    entries=load_entry_headers(ROOT,folder)
    previous_path=folder/'daily_task_chain_result.json'
    previous=json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.is_file() else {}
    resume_path=folder/'daily_resume_checkpoint.json'
    if not previous.get('active_target') and resume_path.is_file():
        previous=json.loads(resume_path.read_text(encoding='utf-8'))
    coordinator = DailyCoordinator(read, lambda: raw, tap, worker,
                                   lambda category, title: bind_choice_header(auth_path, category, title),
                                   log, save, skip_study=auth.get('skip_study_pages', False),
                                   resume_word=lambda view: resume_matches(checkpoint, view),categories=a.categories,entry_headers=entries,
                                   resume_checkpoint=previous)
    final_view = coordinator.run(a.max_tasks)
    (folder/'daily_chain_final.png').write_bytes(raw)
    (folder/'daily_chain_final.json').write_text(json.dumps(final_view, ensure_ascii=False, indent=2), encoding='utf-8')
    if coordinator.report['status']=='all_daily_tasks_verified':
        latest_auth = json.loads(auth_path.read_text(encoding='utf-8'))
        latest_auth.update(status='completed', completion_evidence='daily_chain_final.json')
        auth_path.write_text(json.dumps(latest_auth, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
