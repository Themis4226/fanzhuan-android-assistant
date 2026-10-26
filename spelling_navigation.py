"""The observed spelling row is a word task, separate from numbered cards."""
import re

import daily_navigation as nav
from word_navigation import canonical, is_word_list, wait_for


def spelling_entry(view):
    if not is_word_list(view):
        return None
    headings = [e for e in view['elements'] if e['text'] == '单词拼写'
                and e.get('confidence', 0) >= .9]
    if not headings:
        return None
    if len(headings) != 1:
        raise RuntimeError('单词拼写入口重复，已暂停。')
    h, w = view['height'], view['width']
    y = headings[0]['center'][1]
    courses = [e for e in view['elements'] if e.get('confidence', 0) >= .9
               and w*.15 < e['center'][0] < w*.50
               and abs(e['center'][1]-y) < h*.025
               and len(re.findall(r'[\u4e00-\u9fff]', e['text'])) >= 4
               and e['text'] != '单词拼写' and not re.search(r'[…\u2026]', e['text'])]
    counts = [e for e in view['elements'] if re.fullmatch(r'\d+/\d+', e['text'])
              and e.get('confidence', 0) >= .9 and w*.20 < e['center'][0] < w*.50
              and y < e['center'][1] < y+h*.10]
    if len(courses) != 1 or len(counts) != 1:
        raise RuntimeError('拼写课程或今日进度无法唯一对应，已暂停。')
    done, total = map(int, counts[0]['text'].split('/'))
    if not 0 <= done <= total <= 30 or total < 2:
        raise RuntimeError('拼写题数超出已验证范围。')
    buttons = [e for e in view['elements'] if e.get('confidence', 0) >= .9
               and e['text'] in {'开始', '已完成'} and e['center'][0] > w*.70
               and abs(e['center'][1]-counts[0]['center'][1]) < h*.04]
    if len(buttons) != 1 or (buttons[0]['text'] == '已完成') != (done == total):
        raise RuntimeError('拼写按钮与进度不一致，已暂停。')
    return {'header': courses[0]['text'], 'done': done, 'total': total,
            'completed': done == total, 'center': buttons[0]['center']}


def spelling_page(view, title=None):
    try:
        actual = nav.header(view)['text']
    except RuntimeError:
        return False
    if title is not None and canonical(actual) != canonical(title):
        return False
    h = view['height']
    counts = [e for e in view['elements'] if re.fullmatch(r'\d+/\d+', e['text'])
              and e.get('confidence', 0) >= .85 and h*.105 < e['center'][1] < h*.18]
    # The observed custom keyboard always has its own bottom confirmation key.
    keys = [e for e in view['elements'] if e['text'] == '确认'
            and e.get('confidence', 0) >= .85 and e['center'][1] > h*.90]
    return len(counts) == 1 and len(keys) == 1


def open_spelling(entry, read, tap, log):
    if entry['completed']:
        raise RuntimeError('拼写已完成，拒绝重放。')
    current = read()
    if spelling_entry(current) != entry:
        raise RuntimeError('拼写入口在点击前发生变化。')
    tap(entry['center'])
    expected = f"{entry['done']+1}/{entry['total']}"
    def entered(view):
        return spelling_page(view, entry['header']) and any(
            e['text'] == expected and view['height']*.105 < e['center'][1] < view['height']*.18
            for e in view['elements'])
    view = wait_for(read, entered, '没有进入与拼写课程及进度一致的页面')
    log.event('spelling_entry_opened', header=entry['header'], progress=expected, api_calls=0)
    return view


def finish_spelling(view, state, read, tap, log):
    from spelling_batch import completion_after_last_answer
    if not is_word_list(view):
        if not completion_after_last_answer(state, view):
            raise RuntimeError('未确认最后一道拼写的完成反馈，拒绝返回。')
        current = read()
        if not completion_after_last_answer(state, current):
            raise RuntimeError('拼写完成页在返回前发生变化。')
        button = next(e for e in current['elements'] if e['text'] == '完成'
                      and e.get('confidence', 0) >= .9 and e['center'][1] > current['height']*.7)
        tap(button['center'])
        view = wait_for(read, is_word_list, '拼写完成后未回到单词任务列表')
    entry = spelling_entry(view)
    if (entry is None or canonical(entry['header']) != state['header']
            or entry['total'] != state['total'] or not entry['completed']):
        raise RuntimeError('列表未证实当前课程拼写已完成。')
    log.event('spelling_completed_returned_to_list', header=entry['header'], total=entry['total'], api_calls=0)
    return view
