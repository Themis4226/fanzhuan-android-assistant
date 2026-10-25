"""Observed word-task transitions; no blind back presses or completed-card replay."""
import re
import time

from ocr_view import decode_image


def canonical(text):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]','',text.lower())


def is_completion(view):
    texts=[canonical(e['text']) for e in view['elements']]
    return (any('恭喜你完成卡包' in t for t in texts)
            and any(t in {'complete','本次学习'} for t in texts)
            and any('卡包' in canonical(e['text']) and e['center'][1]<view['height']*.12 for e in view['elements']))


def is_word_list(view):
    return any(e['text']=='单词任务' and e['confidence']>=.85
               and e['center'][1]<view['height']*.12 for e in view['elements'])


def active_card_id(view):
    ids=[]
    for e in view['elements']:
        if e['center'][1]<view['height']*.12 and e['confidence']>=.85:
            m=re.search(r'卡包\s*(\d+)',e['text'])
            if m:ids.append(int(m.group(1)))
    return ids[0] if len(ids)==1 else None


def is_active_card(view):
    headers=[e for e in view['elements'] if '卡包' in e['text'] and e['confidence']>=.9
             and e['center'][1]<view['height']*.12]
    counters=[re.fullmatch(r'学习\s*(\d+)\s*/\s*(\d+)',e['text']) for e in view['elements']]
    counters=[m for m in counters if m]
    return (len(headers)==1 and len(counters)==1
            and 0<=int(counters[0].group(1))<=int(counters[0].group(2)) and int(counters[0].group(2))>0)


def wait_for(read,predicate,reason):
    # Input is sent once. Re-reading a transition never replays the tap.
    for _ in range(4):
        time.sleep(.5)
        view=read()
        if predicate(view):return view
    raise RuntimeError(reason+'；已暂停，不重复点击。')


def return_to_word_list(view,read,tap,log):
    if is_word_list(view):return view
    if not is_completion(view):raise RuntimeError('没有识别到单词卡包完成页，拒绝返回操作。')
    current=read()
    if is_word_list(current):return current
    if not is_completion(current):raise RuntimeError('完成页已变化，停止使用旧按钮。')
    buttons=[e for e in current['elements'] if e['text']=='完成' and e['confidence']>=.9
             and e['center'][1]>current['height']*.70]
    if len(buttons)!=1:raise RuntimeError('完成页底部“完成”按钮不唯一或不清晰。')
    tap(buttons[0]['center'])
    after=wait_for(read,is_word_list,'点击完成后未回到“单词任务”列表')
    log.event('word_completion_returned_to_list',button='完成',api_calls=0)
    return after


def task_tiles(raw,view):
    """Require OCR label + word count + detected tile + completion-ribbon check."""
    import cv2
    import numpy as np
    if not is_word_list(view):raise RuntimeError('当前不是单词任务列表。')
    image=decode_image(raw);height,width=image.shape[:2]
    if (height,width)!=(view['height'],view['width']):raise RuntimeError('列表截图尺寸与 OCR 不一致。')
    low=image.min(axis=2);high=image.max(axis=2)
    mask=((low>=230)&(high<=253)&(high-low<=3)).astype('uint8')
    _,_,stats,_=cv2.connectedComponentsWithStats(mask)
    labels=[e for e in view['elements'] if re.fullmatch(r'卡包\s*\d+',e['text'])
            and e['confidence']>=.9 and e['center'][1]>height*.12]
    if not labels:raise RuntimeError('当前可见列表没有清晰的卡包入口。')
    results=[]
    for label in labels:
        cx,cy=label['center']
        boxes=[tuple(map(int,(x,y,w,h))) for x,y,w,h,area in stats[1:]
               if x<cx<x+w and y<cy<y+h and width*.12<w<width*.4 and width*.1<h<width*.4]
        if len(boxes)!=1:raise RuntimeError('卡包边界不清晰，拒绝按文字猜测点击区域。')
        x,y,w,h=boxes[0]
        counts=[e for e in view['elements'] if re.fullmatch(r'\d+词',e['text']) and e['confidence']>=.85
                and x<e['center'][0]<x+w and cy<e['center'][1]<y+h]
        if len(counts)!=1:raise RuntimeError('卡包词数或边界不明确。')
        badge=image[y:y+h//2,x+w//2:x+w].astype('float32')
        blue,green,red=badge[:,:,0],badge[:,:,1],badge[:,:,2]
        ratio=float(((green>120)&(green>red*1.25)&(blue>red*1.15)).mean())
        text_completed=any(e['text']=='已完成' and x<e['center'][0]<x+w and y<e['center'][1]<y+h//2
                           for e in view['elements'])
        if ratio>=.10:completed=True
        elif ratio<=.02 and not text_completed:completed=False
        else:raise RuntimeError('卡包完成角标不明确，停止选择下一项。')
        results.append({'card_id':int(re.search(r'\d+',label['text']).group()),'text':label['text'],
                        'center':label['center'],'bounds':[x,y,x+w,y+h],
                        'completed':completed,'ribbon_fraction':round(ratio,4)})
        headings=[e for e in view['elements'] if e['confidence']>=.9 and e['bounds'][3]<y
                  and 0<y-e['bounds'][3]<height*.2 and len(re.findall(r'[\u4e00-\u9fff]',e['text']))>=4
                  and e['text'] not in {'未完成闯关','历史闯关错词'} and e['center'][1]>height*.12]
        if headings:results[-1]['course_title']=max(headings,key=lambda e:e['bounds'][3])['text']
    if len({r['card_id'] for r in results})!=len(results):raise RuntimeError('列表中卡包编号重复，无法确定课程。')
    return sorted(results,key=lambda r:(r['bounds'][1],r['bounds'][0]))


def open_pending_card(read,get_raw,tap,log,attempted=()):
    view=read();tiles=task_tiles(get_raw(),view)
    pending=[t for t in tiles if not t['completed']]
    if not pending:return None,view
    target=pending[0]
    if target['card_id'] in attempted:raise RuntimeError('已处理卡包仍显示未完成，暂停以防重复刷题。')
    tap(target['center'])
    def entered(v):
        if not is_active_card(v):return False
        observed_id=active_card_id(v)
        if observed_id is not None:return observed_id==target['card_id']
        # WeChat's capsule can obscure the digits. The clicked tile, its course
        # heading and a fresh learning page must all agree before continuing.
        course=target.get('course_title')
        return bool(course) and any(canonical(e['text']).startswith(canonical(course)+'卡包')
                                   and e['center'][1]<v['height']*.12 for e in v['elements'])
    after=wait_for(read,entered,'点击待完成卡包后未进入预期的学习页面')
    log.event('word_pending_card_opened',card_id=target['card_id'],text=target['text'],api_calls=0)
    return target['card_id'],after


def return_active_to_list(view,read,tap,log):
    if not is_active_card(view):raise RuntimeError('未确认学习页面，不能推断返回入口。')
    current=read()
    if not is_active_card(current):raise RuntimeError('学习页已变化，停止返回。')
    header=next(e for e in current['elements'] if '卡包' in e['text'] and e['center'][1]<current['height']*.12)
    tap([round(current['width']*.05),header['center'][1]])
    after=wait_for(read,is_word_list,'返回列表确认被遮挡的卡包编号失败')
    log.event('word_active_card_returned_for_inventory',api_calls=0)
    return after


def return_to_dashboard(view,read,tap,log):
    if not is_word_list(view):raise RuntimeError('不是单词任务列表，不能推断返回入口。')
    current=read()
    if not is_word_list(current):raise RuntimeError('列表在返回前已变化。')
    title=next(e for e in current['elements'] if e['text']=='单词任务' and e['center'][1]<current['height']*.12)
    tap([round(current['width']*.05),title['center'][1]])
    after=wait_for(read,lambda v:any(e['text']=='今日任务' and e['center'][1]<v['height']*.12 for e in v['elements']),
                   '点击小程序左上返回后未回到今日任务')
    log.event('word_tasks_returned_to_dashboard',api_calls=0)
    return after


def dashboard_word_progress(view):
    labels=[e for e in view['elements'] if e['text']=='单词' and e['confidence']>=.85]
    if len(labels)!=1:return None
    cx,cy=labels[0]['center'];results=[]
    for e in view['elements']:
        m=re.fullmatch(r'已完成\s*(\d+)\s*/\s*(\d+)',e['text'])
        if (m and e['confidence']>=.85 and abs(e['center'][0]-cx)<view['width']*.1
                and 0<e['center'][1]-cy<view['height']*.08):results.append(list(map(int,m.groups())))
    return results[0] if len(results)==1 else None
