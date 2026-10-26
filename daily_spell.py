"""Type one reviewed spelling answer using the observed in-app keyboard."""
import argparse
import json
import re
import time
import difflib
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import cv2
from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices
from assistant_step import fresh_capture, require_wechat
from ocr_view import load_engine, observe, decode_image
from daily_words import chinese
from daily_privacy import notification_free_frame
from fast_observer import ExactFrameObserver


def type_letters(adb, selector, word, centers, width, height):
    """One USB shell round-trip per word; never include confirm or next-page.

    Only generated, checked integer coordinates enter the Android shell. The
    word itself is not shell text. A failing input command stops the sequence.
    """
    if not re.fullmatch('[a-z]{1,30}', word):
        raise ValueError('Lowercase alphabetic word only')
    commands=[]
    for key in word:
        point=centers[key]
        if len(point)!=2 or any(type(c) is not int for c in point):
            raise ValueError('Keyboard coordinates must be integers')
        x,y=point
        if not (0<=x<width and 0<=y<height):
            raise ValueError('Keyboard coordinate outside screen')
        commands.append(f'input tap {x} {y}')
    adb.run(*selector,'shell',' && sleep 0.06 && '.join(commands))

def keyboard_case(view):
    letters=[e['text'] for e in view['elements'] if re.fullmatch('[A-Za-z]',e['text']) and e.get('confidence',0)>=.85
             and view['height']*.70<e['center'][1]<view['height']*.89 and e['center'][0]<view['width']*.90]
    lower=sum(c.islower() for c in letters);upper=len(letters)-lower
    if lower>=4 and lower>upper+2:return 'lower'
    if upper>=4 and upper>lower+2:return 'upper'
    raise RuntimeError('Keyboard letter case is unclear; no further input')


def shift_center(image):
    h,w=image.shape[:2];y0,y1=round(h*.83),round(h*.90);x1=round(w*.16)
    roi=image[y0:y1,:x1];low,high=roi.min(axis=2),roi.max(axis=2)
    mask=((low>=140)&(high<=205)&(high-low<45)).astype('uint8')
    _,_,stats,_=cv2.connectedComponentsWithStats(mask)
    boxes=[(x,y,bw,bh) for x,y,bw,bh,area in stats[1:] if w*.10<bw<w*.16 and h*.045<bh<h*.065
           and x<round(w*.077)<x+bw and y<round(h*.865)-y0<y+bh]
    if len(boxes)!=1:raise RuntimeError('Observed keyboard Shift key boundary is unclear')
    x,y,bw,bh=boxes[0];return (int(x+bw//2),int(y0+y+bh//2))


def type_word(adb,selector,word,centers,width,height,*,read=None,initial_view=None,guard=None):
    if re.fullmatch('[a-z]{2,30}',word):
        return type_letters(adb,selector,word,centers,width,height)
    if not re.fullmatch('[A-Z][a-z]{1,29}',word):raise ValueError('Alphabetic word with optional initial capital only')
    if read is None or initial_view is None or guard is None:raise ValueError('Capital input requires fresh keyboard verification')
    points=[centers['shift']]+[centers[c.lower()] for c in word]
    if any(len(p)!=2 or any(type(c) is not int for c in p) or not 0<=p[0]<width or not 0<=p[1]<height for p in points):
        raise ValueError('Keyboard coordinate outside screen')
    if keyboard_case(initial_view)!='lower':raise RuntimeError('Expected lowercase keyboard before Shift')
    def tap(point):
        guard();adb.run(*selector,'shell','input','tap',*(str(c) for c in point))
        time.sleep(.2);return read()
    shifted=tap(points[0])
    if keyboard_case(shifted)!='upper':raise RuntimeError('Shift did not enable uppercase; no letters sent')
    first=tap(points[1])
    if not input_letters(first).startswith(word[0]):raise RuntimeError('Capital letter did not appear; no further letters sent')
    if keyboard_case(first)=='upper':first=tap(points[0])
    if keyboard_case(first)!='lower':raise RuntimeError('Keyboard did not return to lowercase')
    guard();return type_letters(adb,selector,word[1:],centers,width,height)


def input_letters(view):
    letters=[e for e in view['elements'] if re.fullmatch('[A-Za-z]+',e['text'])
             and e.get('confidence',0)>=.65 and view['height']*.33<e['center'][1]<view['height']*.46
             and view['width']*.10<e['center'][0]<view['width']*.87]
    rows=[]
    for e in sorted(letters,key=lambda e:e['center'][1]):
        if not rows or e['center'][1]-rows[-1][0]['center'][1]>view['height']*.04:rows.append([])
        rows[-1].append(e)
    return ''.join(e['text'] for row in rows for e in sorted(row,key=lambda e:e['center'][0]))


def clear_rejected_case(adb,selector,view,word):
    if not re.fullmatch('[A-Z][a-z]{1,29}',word):raise RuntimeError('Only an observed initial-capital correction is supported')
    hints=[e for e in view['elements'] if re.fullmatch(r'提示\s*'+re.escape(word),e['text']) and e.get('confidence',0)>=.9]
    if len(hints)!=1 or input_letters(view).lower()!=word.lower():
        raise RuntimeError('Rejected input or exact case-sensitive hint does not match; nothing cleared')
    deletes=[e for e in view['elements'] if e['text'] in {'X','⌫'} and e.get('confidence',0)>=.75
             and view['width']*.90<e['center'][0]<view['width']*.98 and view['height']*.84<e['center'][1]<view['height']*.89]
    if len(deletes)!=1:raise RuntimeError('Observed keyboard delete key is unclear; nothing cleared')
    x,y=deletes[0]['center']
    if type(x) is not int or type(y) is not int:raise ValueError('Delete coordinate must be integers')
    adb.run(*selector,'shell',' && sleep 0.06 && '.join([f'input tap {x} {y}']*len(word)))


def spelling_clues(view):
    """One selection/order rule for the model request and every input guard."""
    elements=[e for e in view['elements'] if view['height']*.18<e['center'][1]<view['height']*.55
              and e.get('confidence',0)>=.85 and chinese(e['text']) and not e['text'].startswith('提示')]
    elements.sort(key=lambda e:(e['center'][1],e['center'][0]))
    return [e['text'] for e in elements]


def has_progress(view,expected):
    counters=[e for e in view['elements'] if re.fullmatch(r'\d+/\d+',e['text'])
              and view['height']*.105<e['center'][1]<view['height']*.18 and e.get('confidence',0)>=.85]
    return len(counters)==1 and counters[0]['text']==expected


def answer_current(adb,s,read_frame,initial_raw,initial_view,*,word,clue,progress,clear_case=False,guard=None,expected_header=None):
    """One answer in an existing runtime. Confirm remains a separate guarded tap."""
    if not re.fullmatch('[A-Z]?[a-z]{1,29}',word) or len(word)<2:
        raise ValueError('Alphabetic word with optional initial capital only')
    a=SimpleNamespace(word=word,clue=clue,progress=progress,clear_rejected_case=clear_case)
    guard=guard or (lambda:require_wechat(adb,s))
    last_raw=initial_raw
    def read():
        nonlocal last_raw
        last_raw,v=read_frame()
        return v
    clues=spelling_clues
    v=initial_view
    from daily_navigation import header,has_header
    course=expected_header or header(v)['text']
    if not chinese(a.clue):raise ValueError('Spelling requires a Chinese definition')
    # A slow model response can require a fresh frame. Retry OCR disagreement
    # only before any input, with unchanged course/progress. Never repeat an API
    # request or a letter/confirm command to make this check pass.
    for attempt in range(3):
        if not has_header(v,course):
            raise RuntimeError('拼写课程在输入前变化或无法确认；未输入。')
        if not has_progress(v,a.progress):
            counters=[e['text'] for e in v['elements'] if re.fullmatch(r'\d+/\d+',e['text'])
                      and v['height']*.105<e['center'][1]<v['height']*.18]
            raise RuntimeError(f'拼写进度变化或无法确认（预期{a.progress}，识别{counters}）；未输入。')
        body=clues(v)
        if chinese(a.clue)==chinese(''.join(body)):break
        adb.log.event('spelling_clue_recheck',progress=a.progress,attempt=attempt+1,
                      expected_clue=chinese(a.clue),observed_clue=chinese(''.join(body)),api_calls=0,phone_actions=0)
        if attempt==2:
            raise RuntimeError(f'拼写题意复核连续3次不一致（{a.progress}）；未输入，未再次调用模型。')
        time.sleep(.15);v=read()
    if not a.clear_rejected_case and input_letters(v):
        raise RuntimeError('Spelling input already contains letters; no text appended')
    if a.clear_rejected_case:
        guard();clear_rejected_case(adb,s,v,a.word)
        time.sleep(.3);v=read();body=clues(v)
        if (input_letters(v) or not has_header(v,course) or chinese(a.clue) != chinese(''.join(body))
                or not has_progress(v,a.progress)):
            raise RuntimeError('Rejected input did not clear or question changed; no new input')
        adb.log.event('daily_spelling_rejected_case_cleared',progress=a.progress,api_calls=0)
    image=decode_image(last_raw)
    roi=image[round(v['height']*.35):round(v['height']*.55),round(v['width']*.10):round(v['width']*.87)]
    mask=(cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY)<70).astype('uint8')
    _,_,stats,_=cv2.connectedComponentsWithStats(mask)
    blanks=sum(1 for x,y,w,h,area in stats[1:] if w>v['width']*.04 and h<v['height']*.006 and area>40)
    if blanks!=len(a.word):raise RuntimeError(f'Answer length {len(a.word)} does not match {blanks} visible letter slots')
    centers={}
    rows=('qwertyuiop','asdfghjkl','zxcvbnm')
    # Fit each visible keyboard row from at least four OCR letter anchors.
    for row,letters in enumerate(rows):
        ylo,yhi=((.70,.76),(.77,.83),(.84,.89))[row]
        anchors=[]
        for e in v['elements']:
            key=e['text'].lower()
            if (len(key)==1 and key in letters and e['confidence']>=.85
                    and ylo*v['height']<e['center'][1]<yhi*v['height'] and e['center'][0]<v['width']*.9):
                anchors.append((letters.index(key),e['center'][0],e['center'][1]))
        if len(anchors)<4: raise RuntimeError('Not enough OCR keyboard anchors')
        slope,intercept=np.polyfit([x[0] for x in anchors],[x[1] for x in anchors],1)
        if not v['width']*.085<slope<v['width']*.11: raise RuntimeError('Unexpected key spacing')
        if max(abs(intercept+slope*i-x) for i,x,_ in anchors)>12: raise RuntimeError('Keyboard geometry inconsistent')
        y=round(float(np.median([x[2] for x in anchors])))
        for i,key in enumerate(letters):centers[key]=(round(intercept+slope*i),y)
    guard()
    if a.word[0].isupper():centers['shift']=shift_center(image)
    def read_case():
        current=read()
        if ((current['width'],current['height'])!=(v['width'],v['height'])
                or not has_header(current,course)
                or chinese(a.clue) != chinese(''.join(clues(current)))
                or not has_progress(current,a.progress)):
            raise RuntimeError('Question changed during case input')
        return current
    type_word(adb,s,a.word,centers,v['width'],v['height'],read=read_case,initial_view=v,guard=guard)
    typed=read()
    if (difflib.SequenceMatcher(None,chinese(''.join(clues(typed))),chinese(''.join(body))).ratio()<.96
            or not has_header(typed,course) or not has_progress(typed,a.progress)):
        raise RuntimeError('Question changed while typing')
    if a.word[0].isupper() and not input_letters(typed).startswith(a.word[0]):
        raise RuntimeError('Initial capital was not observed; no confirm sent')
    buttons=[e for e in typed['elements'] if e['text']=='确认' and e['confidence']>=.85 and e['center'][1]>v['height']*.9]
    if len(buttons)!=1: raise RuntimeError('Keyboard confirm not clearly visible')
    guard()
    adb.run(*s,'shell','input','tap',*(str(c) for c in buttons[0]['center']))
    time.sleep(.7);after=read()
    n,total=map(int,a.progress.split('/'))
    from spelling_batch import completion_after_last_answer
    state={'progress':a.progress,'total':total}
    if not ((n<total and has_header(after,course) and has_progress(after,f'{n+1}/{total}'))
            or completion_after_last_answer(state,after)):
        raise RuntimeError('Spelling submission did not advance; inspect actual feedback')
    adb.log.event('daily_spelling_answer',word=a.word,clue=body,progress=a.progress,api_calls=0,source='reviewed_OCR_answer')
    print(json.dumps({'submitted_word':a.word,'after':[e['text'] for e in after['elements'] if 100<e['center'][1]<after['height']*.65]},ensure_ascii=False),flush=True)
    return last_raw,after


class SpellingRuntime:
    """Reuse one OCR/USB runtime and only the most recent, short-lived read."""
    def __init__(self,adb,selector,read_frame,guard,*,clock=None):
        self.adb,self.selector,self.read_frame,self.guard=adb,selector,read_frame,guard
        self.clock=clock or time.monotonic
        self.frame=None;self.read_at=0

    def read(self):
        self.frame=self.read_frame();self.read_at=self.clock()
        return self.frame

    def current(self):
        if self.frame is None or self.clock()-self.read_at>2:
            return self.read()
        return self.frame

    def answer(self,word,clue,progress,*,clear_case=False,expected_header=None):
        raw,view=self.current()
        result=answer_current(self.adb,self.selector,self.read,raw,view,word=word,
                              clue=clue,progress=progress,clear_case=clear_case,guard=self.guard,expected_header=expected_header)
        self.frame=result
        return result


def create_runtime(folder,*,no_cache=False):
    def authorization():
        auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
        if auth.get('status')!='active' or not auth.get('automatic_answers'):
            raise RuntimeError('No active spelling authorization')
        return auth
    auth=authorization();log=RunLog(folder);adb=Adb(find_adb(None),20,log)
    selector=adb.selector(adb.select_usb(parse_devices(adb.text('devices','-l')),auth.get('serial')))
    engine=load_engine();engine.use_cls=False
    observer=ExactFrameObserver(engine,observe,log=log,enabled=not no_cache)
    def guard():
        authorization();require_wechat(adb,selector)
    def read():
        def capture():
            guard();return fresh_capture(adb,selector)
        raw,view=notification_free_frame(capture,lambda data:observer(data,live=True),log)
        (folder/'latest.png').write_bytes(raw)
        (folder/'latest.json').write_text(json.dumps(view,ensure_ascii=False,indent=2),encoding='utf-8')
        return raw,view
    return SpellingRuntime(adb,selector,read,guard)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--word');p.add_argument('--clue');p.add_argument('--progress')
    p.add_argument('--batch',action='store_true')
    p.add_argument('--count',type=int,default=25)
    p.add_argument('--no-ocr-cache',action='store_true')
    p.add_argument('--clear-rejected-case',action='store_true')
    a=p.parse_args()
    if a.batch:
        if a.word or a.clue or a.progress or a.clear_rejected_case:p.error('batch cannot be combined with a single answer')
        from spelling_batch import main as run_batch
        return run_batch(a.count,no_cache=a.no_ocr_cache)
    if not all((a.word,a.clue,a.progress)):p.error('word, clue and progress are required for one answer')
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    runtime=create_runtime(folder,no_cache=a.no_ocr_cache)
    runtime.answer(a.word,a.clue,a.progress,clear_case=a.clear_rejected_case)

if __name__=='__main__':main()
