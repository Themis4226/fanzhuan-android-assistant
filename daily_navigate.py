"""Inspect or perform one observed daily-task navigation action."""
import argparse
import json
import time
from pathlib import Path
from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices, timestamp
from assistant_step import fresh_capture, require_wechat
from ocr_view import load_engine, observe
from daily_privacy import assert_no_notification

def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group()
    g.add_argument('--tap')
    g.add_argument('--back', action='store_true')
    g.add_argument('--up', action='store_true')
    g.add_argument('--top-back', metavar='CURRENT_HEADER', help='Use the observed page back arrow after checking its header')
    g.add_argument('--next-page', metavar='CURRENT_COUNTER', help='Use visually verified bottom-right page arrow')
    g.add_argument('--previous-page',metavar='CURRENT_COUNTER')
    g.add_argument('--play-listening', action='store_true')
    g.add_argument('--close-completion', action='store_true')
    p.add_argument('--evidence')
    p.add_argument('--tap-max-y',type=int,help='Limit exact text target to the observed upper dialog area')
    a=p.parse_args()
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if not auth.get('automatic_answers'): raise RuntimeError('No daily authorization')
    if auth.get('status')=='completed' and any((a.tap,a.back,a.up,a.top_back,a.next_page,a.previous_page,a.play_listening)):
        raise RuntimeError('Daily session completed; mutations are disabled')
    adb=Adb(find_adb(None),20,RunLog(folder))
    selector=adb.selector(adb.select_usb(parse_devices(adb.text('devices','-l')),auth.get('serial')))
    engine=load_engine(); engine.use_cls=False
    def read():
        require_wechat(adb,selector)
        raw=fresh_capture(adb,selector)
        view=observe(raw,engine=engine,live=True)
        assert_no_notification(view)
        (folder/'latest.png').write_bytes(raw)
        (folder/'latest.json').write_text(json.dumps(view,ensure_ascii=False,indent=2),encoding='utf-8')
        return raw,view
    raw,view=read()
    if a.tap:
        targets=[e for e in view['elements'] if e['text']==a.tap and e['confidence']>=.85
                 and (a.tap_max_y is None or e['center'][1]<a.tap_max_y)]
        if len(targets)!=1: raise RuntimeError('Expected one confident exact target: '+a.tap)
        e=targets[0]; require_wechat(adb,selector)
        adb.run(*selector,'shell','input','tap',*(str(v) for v in e['center']))
        adb.log.event('daily_navigation_tap',text=a.tap,center=e['center'])
    elif a.close_completion:
        texts={e['text'] for e in view['elements']}
        if not {'今日任务','100%','完成今日所有任务！'}.issubset(texts):
            raise RuntimeError('Expected all-tasks completion modal')
        # X visually verified above the top-right corner of the reward modal.
        rates=[e for e in view['elements'] if e['text']=='100%' and e['center'][1]<view['height']*.25]
        if len(rates)!=1:raise RuntimeError('Completion close-button anchor ambiguous')
        close_y=rates[0]['center'][1]+round(view['width']*.028)
        adb.run(*selector,'shell','input','tap',str(round(view['width']*.851)),str(close_y))
        adb.log.event('daily_completion_modal_closed',source='explicit_user_request')
    elif a.play_listening:
        counters=[e for e in view['elements'] if e['text']=='已播放0遍' and e['bounds'][1]>view['height']*.8]
        if len(counters)!=1 or not any('长对话' in e['text'] and e['bounds'][1]<260 for e in view['elements']):
            raise RuntimeError('Expected unplayed listening page')
        c=counters[0]['center']
        adb.run(*selector,'shell','input','tap',str(c[0]),str(c[1]-round(view['width']*.093)))
        adb.log.event('daily_listening_play_button')
    elif a.next_page or a.previous_page:
        requested=a.next_page or a.previous_page
        counters=[e for e in view['elements'] if e['text']==requested and e['bounds'][1]>view['height']*.9]
        if len(counters)!=1: raise RuntimeError('Expected bottom page counter')
        adb.run(*selector,'shell','input','tap',str(round(view['width']*(.925 if a.next_page else .075))),str(counters[0]['center'][1]))
        adb.log.event('daily_page_arrow',counter=requested,direction='next' if a.next_page else 'previous')
    elif a.top_back:
        headers=[e for e in view['elements'] if e['text']==a.top_back and e['bounds'][1]<view['height']*.10]
        if len(headers)!=1: raise RuntimeError('Unexpected page header')
        # Arrow visually verified at the left of this app navigation header.
        adb.run(*selector,'shell','input','tap',str(round(view['width']*.05)),str(headers[0]['center'][1]))
        adb.log.event('daily_page_back_arrow',header=a.top_back)
    elif a.back:
        adb.run(*selector,'shell','input','keyevent','4')
    elif a.up:
        w,h=view['width'],view['height']
        adb.run(*selector,'shell','input','swipe',str(w//2),str(round(h*.75)),str(w//2),str(round(h*.3)),'600')
    if a.tap or a.back or a.up or a.top_back or a.next_page or a.previous_page or a.play_listening or a.close_completion:
        time.sleep(1.3); raw,view=read()
    if a.evidence:
        name=Path(a.evidence).name
        if name!=a.evidence: raise ValueError('Evidence filename only')
        (folder/(name+'.png')).write_bytes(raw)
        (folder/(name+'.json')).write_text(json.dumps(view,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{'text':e['text'],'center':e['center'],'confidence':e['confidence']} for e in view['elements'] if 100<e['bounds'][1]<view['height']-100],ensure_ascii=False),flush=True)

if __name__=='__main__': main()
