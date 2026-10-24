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
from daily_privacy import assert_no_notification
from fast_observer import ExactFrameObserver

def main():
    p=argparse.ArgumentParser();p.add_argument('--header',required=True);p.add_argument('--count',type=int,default=20)
    p.add_argument('--no-ocr-cache',action='store_true');a=p.parse_args()
    if not 1<=a.count<=30:p.error('count must be 1..30')
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if not auth.get('automatic_answers') or auth.get('status')=='completed':raise RuntimeError('No active daily authorization')
    adb=Adb(find_adb(None),20,RunLog(folder));s=adb.selector(adb.select_usb(parse_devices(adb.text('devices','-l')),auth.get('serial')))
    engine=load_engine();engine.use_cls=False
    observer=ExactFrameObserver(engine,observe,log=adb.log,enabled=not a.no_ocr_cache)
    last_raw=None
    def completed(v):return any('当前卡包已完成' in e['text'] or '任务已完成' in e['text'] for e in v['elements'])
    def read():
        nonlocal last_raw
        require_wechat(adb,s);last_raw=fresh_capture(adb,s);v=observer(last_raw,live=True)
        assert_no_notification(v)
        (folder/'latest.png').write_bytes(last_raw);(folder/'latest.json').write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
        if not completed(v) and not any(canonical(e['text'])==canonical(a.header) and e['center'][1]<260 for e in v['elements']):raise RuntimeError('Listening header changed')
        return v
    def counter(v):
        results=[e for e in v['elements'] if re.fullmatch(r'\d+/\d+',e['text']) and e['center'][1]>v['height']*.9]
        if len(results)!=1:raise RuntimeError('Listening page counter ambiguous')
        return results[0]
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
