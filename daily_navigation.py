"""Navigation for the observed daily dashboard and course-card lists."""
import re

from ocr_view import decode_image
from word_navigation import canonical, wait_for

SUPPORTED = ('单词', '语法', '阅读', '听力')
CATEGORIES = SUPPORTED + ('写作', '技巧', '口语')


def header(view):
    titles = [e for e in view['elements'] if e.get('confidence', 0) >= .85
              and view['height'] * .045 < e['center'][1] < view['height'] * .10
              and view['width'] * .20 < e['center'][0] < view['width'] * .75]
    if len(titles) != 1:
        raise RuntimeError('页面标题不唯一；请回到今日任务首页后再运行。')
    return titles[0]


def has_header(view, title):
    try:
        return canonical(header(view)['text']) == canonical(title)
    except RuntimeError:
        return False


def all_complete_modal(view):
    texts = {canonical(e['text']) for e in view['elements'] if e.get('confidence', 0) >= .85}
    return has_header(view, '今日任务') and {'100', '完成今日所有任务'}.issubset(texts)


def dashboard(view):
    if not has_header(view, '今日任务') or all_complete_modal(view):
        raise RuntimeError('需要无遮挡的今日任务首页。')
    counters = [e for e in view['elements'] if re.fullmatch(r'已完成\s*\d+\s*/\s*\d+', e['text'])
                and e.get('confidence', 0) >= .85]
    result = {}
    used = set()
    for category in CATEGORIES:
        labels = [e for e in view['elements'] if e['text'] == category and e.get('confidence', 0) >= .75]
        if len(labels) != 1:
            raise RuntimeError('首页分类缺失或重复：' + category)
        x, y = labels[0]['center']
        matches = [(i, e) for i, e in enumerate(counters)
                   if abs(e['center'][0] - x) < view['width'] * .10
                   and 0 < e['center'][1] - y < view['height'] * .06]
        if len(matches) != 1 or matches[0][0] in used:
            raise RuntimeError('无法将完成数唯一对应到分类：' + category)
        i, e = matches[0]; used.add(i)
        n, total = map(int, re.findall(r'\d+', e['text']))
        if not 0 <= n <= total <= 100:
            raise RuntimeError('首页完成数不合法。')
        # A dim, unused category can have a weaker label while its 0/0 count
        # remains clear. It will never become a tap target. Positive categories
        # retain the original label threshold; aggregate/unique-count checks stay.
        if labels[0].get('confidence',0)<.85 and ((n,total)!=(0,0) or e.get('confidence',0)<.95):
            raise RuntimeError('首页分类文字不清晰：'+category)
        result[category] = {'done': n, 'total': total, 'center': labels[0]['center']}
    if len(used) != len(counters):
        raise RuntimeError('首页有尚未适配的分类，拒绝遗漏任务。')
    totals = [e for e in view['elements'] if re.fullmatch(r'\d+/\d+', e['text'])
              and e.get('confidence', 0) >= .85 and e['center'][1] < view['height'] * .25]
    aggregate = (sum(v['done'] for v in result.values()), sum(v['total'] for v in result.values()))
    if len(totals) != 1 or tuple(map(int, totals[0]['text'].split('/'))) != aggregate or aggregate[1] == 0:
        raise RuntimeError('首页总计与各分类计数不一致。')
    return result


def next_category(progress,allowed=SUPPORTED):
    unsupported = [k for k, v in progress.items() if k not in SUPPORTED and v['done'] < v['total']]
    if tuple(allowed)!=SUPPORTED:unsupported=[]
    if unsupported:
        raise RuntimeError('以下任务尚未适配，已暂停：' + '、'.join(unsupported))
    return next((k for k in allowed if progress[k]['done'] < progress[k]['total']), None)


def verify_progress(before, after, category):
    if before.keys() != after.keys():
        raise RuntimeError('首页分类发生变化。')
    for k in before:
        if before[k]['total'] != after[k]['total']:
            raise RuntimeError('任务总数发生变化，停止本轮调度。')
        delta = after[k]['done'] - before[k]['done']
        if (k == category and delta <= 0) or (k != category and delta != 0):
            raise RuntimeError('首页未证实本项进度增加，或其他分类意外变化；不会自动重做。')


def card_completed(view):
    return any('卡包已完成' in canonical(e['text']) or '任务已完成' in canonical(e['text'])
               for e in view['elements'] if e.get('confidence', 0) >= .85)


def is_menu(view, category):
    return has_header(view, category + '任务') and not card_completed(view)


def course_tiles(raw, view, category):
    """Bind each N篇 label to a gray card, title, course and completion ribbon."""
    import cv2
    import numpy as np
    if category not in {'语法', '阅读', '听力'} or not is_menu(view, category):
        raise RuntimeError('不是已适配的科目列表。')
    image = decode_image(raw)
    height, width = image.shape[:2]
    if (height, width) != (view['height'], view['width']):
        raise RuntimeError('截图与文字坐标尺寸不符。')
    low, high = image.min(axis=2), image.max(axis=2)
    mask = ((low >= 230) & (high <= 253) & (high-low <= 3)).astype('uint8')
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    counts = [e for e in view['elements'] if re.fullmatch(r'\d+篇', e['text'])
              and e.get('confidence', 0) >= .9 and e['center'][1] > height * .12]
    if not counts:
        raise RuntimeError('列表中未找到清晰的篇数；不猜测入口。')
    result = []
    for count in counts:
        cx, cy = count['center']
        boxes = [tuple(map(int, (x, y, w, h))) for x, y, w, h, area in stats[1:]
                 if x < cx < x+w and y < cy < y+h and width*.12 < w < width*.4 and width*.1 < h < width*.4]
        if len(boxes) != 1:
            raise RuntimeError('任务卡片边界不清晰。')
        x, y, w, h = boxes[0]
        labels = [e for e in view['elements'] if e.get('confidence', 0) >= .9
                  and x < e['center'][0] < x+w and y+h*.18 < e['center'][1] < cy
                  and canonical(e['text']) != '已完成']
        if len(labels) != 1:
            raise RuntimeError('任务标题不唯一或不清晰。')
        label = labels[0]
        headings = [e for e in view['elements'] if e.get('confidence', 0) >= .9
                    and e['center'][0] > width*.1 and height*.12 < e['bounds'][3] < y
                    and 0 < y-e['bounds'][3] < height*.2]
        if not headings:
            raise RuntimeError('缺少任务所属课程标题。')
        course = max(headings, key=lambda e: (e['bounds'][3], e['center'][0]))['text']
        band = image[y:y+h//2, x+w//2:x+w].astype('float32')
        blue, green, red = band[:, :, 0], band[:, :, 1], band[:, :, 2]
        ratio = float(((green > 120) & (green > red*1.25) & (blue > red*1.15)).mean())
        badge = any(canonical(e['text']) == '已完成' and x < e['center'][0] < x+w
                    and y < e['center'][1] < y+h//2 for e in view['elements'])
        if ratio >= .10:
            done = True
        elif ratio <= .02 and not badge:
            done = False
        else:
            raise RuntimeError('完成角标不明确，停止选择任务。')
        result.append({'key': category+'|'+canonical(course)+'|'+canonical(label['text']),
                       'title': label['text'], 'course': course, 'center': label['center'],
                       'pages': int(re.search(r'\d+', count['text']).group()),
                       'completed': done, 'bounds': [x, y, x+w, y+h]})
    if len({t['key'] for t in result}) != len(result):
        raise RuntimeError('任务标题重复，不能唯一定位。')
    return sorted(result, key=lambda t: (t['bounds'][1], t['bounds'][0]))


def page_counter(view):
    counters = [e for e in view['elements'] if re.fullmatch(r'\d+/\d+', e['text'])
                and e.get('confidence', 0) >= .85 and e['center'][1] > view['height']*.9]
    if len(counters) != 1:
        return None
    n, total = map(int, counters[0]['text'].split('/'))
    return (n, total) if 1 <= n <= total <= 50 else None


def entered_card(view, target, expected_header=None):
    counter = page_counter(view)
    if counter is None or counter[1] != target['pages']:
        return False
    try:
        title = canonical(header(view)['text'])
    except RuntimeError:
        return False
    if expected_header is not None:
        return title==canonical(expected_header)
    preview = canonical(target['title'])
    card = re.fullmatch(r'卡包0*(\d+)', preview)
    if card:
        observed = re.search(r'卡包0*(\d+)', title)
        return (title.startswith(canonical(target['course'])+'卡包')
                and (observed is None or int(observed.group(1)) == int(card.group(1))))
    return len(preview) >= 3 and title.startswith(preview)


def back_from_menu(view, category, read, tap, log):
    current = read()
    if not is_menu(current, category):
        raise RuntimeError('任务列表已变化，停止返回。')
    tap([round(current['width']*.05), header(current)['center'][1]])
    after = wait_for(read, lambda v: has_header(v, '今日任务'), '未返回今日任务首页')
    log.event('daily_category_returned_to_dashboard', category=category, api_calls=0)
    return after


def finish_card(view, category, expected_header, read, tap, log):
    if is_menu(view, category):
        return view
    if not card_completed(view):
        raise RuntimeError('子脚本已结束，但未观察到卡包完成反馈。')
    current = read()
    if not card_completed(current):
        raise RuntimeError('卡包完成页面已变化。')
    if not (has_header(current, expected_header) or has_header(current, category+'任务')):
        raise RuntimeError('完成反馈来自意外的课程。')
    buttons = [e for e in current['elements'] if e['text'] == '完成' and e.get('confidence', 0) >= .9
               and e['center'][1] > current['height']*.70]
    if len(buttons) != 1:
        raise RuntimeError('完成按钮不唯一；保留现场。')
    tap(buttons[0]['center'])
    after = wait_for(read, lambda v: is_menu(v, category), '卡包完成后未返回原科目列表')
    log.event('daily_course_completion_returned', category=category, api_calls=0)
    return after


def dismiss_all_complete(view, read, tap, log):
    current = read()
    if not all_complete_modal(current):
        raise RuntimeError('今日完成弹窗已变化。')
    rates = [e for e in current['elements'] if e['text'] == '100%'
             and e['center'][1] < current['height']*.25 and e.get('confidence', 0) >= .9]
    if len(rates) != 1:
        raise RuntimeError('完成弹窗关闭位置缺少唯一锚点。')
    tap([round(current['width']*.851), rates[0]['center'][1]+round(current['width']*.028)])
    after = wait_for(read, lambda v: has_header(v, '今日任务') and not all_complete_modal(v), '完成弹窗未关闭')
    log.event('daily_all_complete_modal_closed', api_calls=0)
    return after
