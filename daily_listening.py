"""Play each actual listening clip once, wait for UI feedback, then advance."""
import argparse
import json
import re
import time
from pathlib import Path
from phone_capture import ROOT,Adb,RunLog,find_adb,parse_devices
from assistant_step import fresh_capture,require_wechat
from ocr_view import load_engine,observe
from daily_words import canonical
from daily_privacy import notification_free_frame
from fast_observer import ExactFrameObserver
from daily_navigation import card_completed


def completed(view):
    return card_completed(view)

def intro_confirm(view):
    """Recognize only the observed playback-help dialog, never a generic confirm."""
    elements=[e for e in view['elements'] if e.get('confidence',0)>=.9]
    titles=[e for e in view['elements'] if e['text']=='温馨提示']
    if not titles:return None
    if len(titles)!=1 or titles[0].get('confidence',0)<.85:
        raise RuntimeError('听力提示标题不清晰，保留页面。')
    lines=sorted((e for e in elements if view['height']*.40<e['center'][1]<view['height']*.55),
                 key=lambda e:(e['center'][1],e['center'][0]))
    body=re.sub(r'\s+','',''.join(e['text'] for e in lines))
    if not all(marker in body for marker in ('当听力无法播放时','检查网络是否通畅','删除小程序重新打开')):
        raise RuntimeError('未知听力弹窗，保留页面。')
    buttons=[e for e in elements if e['text']=='确认' and view['height']*.55<e['center'][1]<view['height']*.70]
    if len(buttons)!=1:raise RuntimeError('听力说明确认按钮不唯一。')
    return buttons[0]['center']


def page_counter(view):
    results=[e for e in view['elements'] if re.fullmatch(r'\d+/\d+',e['text'])
             and e.get('confidence',1)>=.85 and e['center'][1]>view['height']*.9]
    if len(results)!=1:raise RuntimeError('Listening page counter ambiguous')
    n,total=map(int,results[0]['text'].split('/'))
    if not 1<=n<=total<=30:raise RuntimeError('Listening page counter outside supported range')
    return results[0]


def dismiss_intro(view,read,tap,log):
    button=intro_confirm(view)
    if button is None:return view
    current=read()
    if intro_confirm(current)!=button or page_counter(current)['text']!=page_counter(view)['text']:
        raise RuntimeError('听力说明弹窗在确认前发生变化。')
    tap(button)
    log.event('daily_listening_intro_dismissed',page=page_counter(view)['text'],api_calls=0)
    # Only reread; never repeat the confirmation if the network/UI is slow.
    for _ in range(4):
        time.sleep(.35);after=read()
        if completed(after) or page_counter(after)['text']!=page_counter(view)['text']:
            raise RuntimeError('关闭听力说明时页面意外改变。')
        if intro_confirm(after) is None:return after
    raise RuntimeError('听力说明弹窗未关闭，不重复点击。')

def main():
    p=argparse.ArgumentParser();p.add_argument('--header',required=True);p.add_argument('--count',type=int,default=20)
    p.add_argument('--no-ocr-cache',action='store_true');a=p.parse_args()
    if not 1<=a.count<=30:p.error('count must be 1..30')
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if not auth.get('automatic_answers') or auth.get('status') in {'completed','closed'}:raise RuntimeError('No active daily authorization')
    adb=Adb(find_adb(None),20,RunLog(folder));s=adb.selector(adb.select_usb(parse_devices(adb.text('devices','-l')),auth.get('serial')))
    engine=load_engine();engine.use_cls=False
    observer=ExactFrameObserver(engine,observe,log=adb.log,enabled=not a.no_ocr_cache)
    last_raw=None
    def read_raw():
        nonlocal last_raw
        def capture():
            require_wechat(adb,s)
            return fresh_capture(adb,s)
        last_raw,v=notification_free_frame(capture,lambda data:observer(data,live=True),adb.log)
        (folder/'latest.png').write_bytes(last_raw);(folder/'latest.json').write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
        if not completed(v) and not any(canonical(e['text'])==canonical(a.header) and e['center'][1]<260 for e in v['elements']):raise RuntimeError('Listening header changed')
        return v
    def tap(center):
        require_wechat(adb,s)
        adb.run(*s,'shell','input','tap',*(str(c) for c in center))
    def read():
        return dismiss_intro(read_raw(),read_raw,tap,adb.log)
    counter=page_counter
    def play_count(v):
        results=[re.fullmatch(r'已播放第?(\d+)遍',e['text']) for e in v['elements']]
        counts=[int(m.group(1)) for m in results if m]
        return counts[0] if len(counts)==1 else None
    v=read()
    for step in range(a.count):
        if completed(v):
            (folder/'listening_completion.png').write_bytes(last_raw)
            print(json.dumps({'status':'listening_completion_observed'},ensure_ascii=False),flush=True);return
        c=counter(v);current=c['text'];n,total=map(int,current.split('/'))
        # Wait for the new page's playback badge to settle. Each page must get
        # its own explicit play action; do not reuse the previous page's badge.
        deadline=time.monotonic()+90
        while play_count(v) is None:
            if time.monotonic()>deadline:raise RuntimeError('Playback state did not settle')
            time.sleep(2);v=read()
            if counter(v)['text']!=current:raise RuntimeError('Page changed during playback')
        time.sleep(1);v=read()
        if counter(v)['text']!=current:raise RuntimeError('Page changed before play')
        initial_count=play_count(v)
        if initial_count is None:raise RuntimeError('Playback started externally; inspect state')
        buttons=[e for e in v['elements'] if re.fullmatch(r'已播放第?\d+遍',e['text']) and e['center'][1]>v['height']*.8]
        if len(buttons)!=1:raise RuntimeError('Playback badge not unique')
        x,y=buttons[0]['center'];require_wechat(adb,s)
        adb.run(*s,'shell','input','tap',str(x),str(y-round(v['width']*.093)))
        adb.log.event('daily_listening_play',page=current,prior_play_count=initial_count,api_calls=0)
        print(json.dumps({'page':current,'status':'play_pressed','prior_play_count':initial_count},ensure_ascii=False),flush=True)
        deadline=time.monotonic()+90
        while play_count(v)!=initial_count+1:
            if time.monotonic()>deadline:raise RuntimeError('Playback count did not increase; no retry')
            time.sleep(2);v=read()
            if counter(v)['text']!=current:raise RuntimeError('Listening page changed while waiting')
        require_wechat(adb,s)
        adb.run(*s,'shell','input','tap',str(round(v['width']*.925)),str(counter(v)['center'][1]))
        time.sleep(.7);after=read()
        if not completed(after) and counter(after)['text']!=f'{n+1}/{total}':raise RuntimeError('Listening next page did not advance')
        adb.log.event('daily_listening_playback_verified',page=current,feedback='one full playback observed',api_calls=0)
        print(json.dumps({'page':current,'status':'played_once_and_advanced','completion':completed(after)},ensure_ascii=False),flush=True)
        if completed(after):
            (folder/'listening_completion.png').write_bytes(last_raw);return
        v=after
    print('Bounded listening batch finished',flush=True)

if __name__=='__main__':main()
