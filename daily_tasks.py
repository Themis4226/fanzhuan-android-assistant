"""Bounded daily-task coordinator, driven by verified dashboard progress."""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

from assistant_step import fresh_capture, require_wechat
from daily_privacy import assert_no_notification
from daily_word_tasks import run_card_worker
from fast_observer import ExactFrameObserver
from ocr_view import load_engine, observe
from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices
import daily_navigation as nav


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


class DailyCoordinator:
    def __init__(self, read, get_raw, tap, worker, authorize_choice, log, save, *, skip_study=False):
        self.read, self.get_raw, self.tap = read, get_raw, tap
        self.worker, self.authorize_choice, self.log, self.save = worker, authorize_choice, log, save
        self.skip_study = skip_study
        self.report = {'status': 'running', 'date': date.today().isoformat(), 'completed': []}

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
            # A confirmed list can return by the app's own top-left arrow.
            # Unknown details are left intact; launch daily mode at the dashboard.
            for category in nav.SUPPORTED:
                if nav.is_menu(view, category):
                    view = nav.back_from_menu(view, category, self.read, self.tap, self.log)
                    break
            for step in range(max_tasks+1):
                if nav.all_complete_modal(view):
                    view = nav.dismiss_all_complete(view, self.read, self.tap, self.log)
                before = nav.dashboard(view)
                self.record(progress=before)
                category = nav.next_category(before)
                if category is None:
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
                    view = nav.wait_for(self.read, lambda v: nav.entered_card(v, target), '未进入与卡片标题及篇数一致的课程')
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
    a = p.parse_args(argv)
    if not 1 <= a.max_tasks <= 30:
        p.error('max-tasks must be 1..30')
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
        require_wechat(adb, selector); captured = fresh_capture(adb, selector)
        latest = observer(captured, live=True); assert_no_notification(latest)
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

    coordinator = DailyCoordinator(read, lambda: raw, tap, worker,
                                   lambda category, title: bind_choice_header(auth_path, category, title),
                                   log, save, skip_study=auth.get('skip_study_pages', False))
    final_view = coordinator.run(a.max_tasks)
    (folder/'daily_chain_final.png').write_bytes(raw)
    (folder/'daily_chain_final.json').write_text(json.dumps(final_view, ensure_ascii=False, indent=2), encoding='utf-8')
    latest_auth = json.loads(auth_path.read_text(encoding='utf-8'))
    latest_auth.update(status='completed', completion_evidence='daily_chain_final.json')
    auth_path.write_text(json.dumps(latest_auth, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
