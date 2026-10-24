"""User-authorized arbitrary practice choices through ordinary page controls."""
import argparse
import json
import re
import time
from pathlib import Path
from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices
from assistant_step import fresh_capture, require_wechat
from ocr_view import load_engine, observe, decode_image
from daily_words import canonical
from daily_privacy import assert_no_notification

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--header',required=True)
    p.add_argument('--count',type=int,default=12)
    p.add_argument('--allow-info-and-scroll',action='store_true')
    a=p.parse_args()
    if not 1<=a.count<=20: p.error('count must be 1..20')
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if not auth.get('automatic_answers') or auth.get('status')=='completed' or a.header not in auth.get('arbitrary_choice_headers',[]):
        raise RuntimeError('Explicit arbitrary-choice authorization for this header is missing')
    adb=Adb(find_adb(None),20,RunLog(folder))
    s=adb.selector(adb.select_usb(parse_devices(adb.text('devices','-l')),auth.get('serial')))
    engine=load_engine(); engine.use_cls=False
    def read():
        require_wechat(adb,s)
        raw=fresh_capture(adb,s); v=observe(raw,engine=engine,live=True)
        assert_no_notification(v)
        (folder/'latest.png').write_bytes(raw)
        (folder/'latest.json').write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
        if not any(canonical(e['text'])==canonical(a.header) and e['bounds'][1]<260 for e in v['elements']):
            raise RuntimeError('Page header changed')
        return raw,v
    for _ in range(a.count):
        raw,v=read()
        if any('卡包已完成' in e['text'] for e in v['elements']):
            (folder/'choice_completion.png').write_bytes(raw)
            print(json.dumps({'status':'completion_observed','header':a.header},ensure_ascii=False),flush=True)
            return
        counters=[e for e in v['elements'] if re.fullmatch(r'\d+/\d+',e['text']) and e['bounds'][1]>v['height']*.9]
        if len(counters)!=1: raise RuntimeError('Page counter ambiguous')
        counter=counters[0]; n,total=map(int,counter['text'].split('/'))
        choices=[e for e in v['elements'] if e['text']=='A' and e['confidence']>=.85
                 and e['center'][0]<v['width']*.15 and 280<e['center'][1]<v['height']*.9]
        if not choices and a.allow_info_and_scroll and any('先看题目' in e['text'] for e in v['elements']):
            adb.run(*s,'shell','input','tap',str(round(v['width']*.925)),str(counter['center'][1]))
            time.sleep(.6)
            _,next_view=read()
            if not any(e['text']==f'{n+1}/{total}' for e in next_view['elements']):
                raise RuntimeError('Information page did not advance')
            print(json.dumps({'info_page_skipped':counter['text'],'api_calls':0},ensure_ascii=False),flush=True)
            continue
        if not choices and a.allow_info_and_scroll:
            for attempt in range(8):
                adb.run(*s,'shell','input','swipe',str(v['width']//2),str(round(v['height']*.75)),str(v['width']//2),str(round(v['height']*.30)),'600')
                time.sleep(.4)
                _,v=read()
                if not any(e['text']==counter['text'] for e in v['elements']):raise RuntimeError('Page changed during scroll')
                choices=[e for e in v['elements'] if e['text']=='A' and e['confidence']>=.85
                         and e['center'][0]<v['width']*.15 and 280<e['center'][1]<v['height']*.9]
                if choices:break
        if len(choices)!=1: raise RuntimeError('First option not clearly visible; inspect page')
        target=choices[0]
        adb.run(*s,'shell','input','tap',*(str(c) for c in target['center']))
        time.sleep(.4)
        after_raw,after=read()
        after_counter=[e['text'] for e in after['elements'] if re.fullmatch(r'\d+/\d+',e['text']) and e['bounds'][1]>after['height']*.9]
        if after_counter!=[counter['text']]: raise RuntimeError('Unexpected page after option tap')
        pixels=decode_image(after_raw)
        x,y=target['center']; radius=round(v['width']*.04)
        band=pixels[y-radius:y+radius,x-radius:x+radius].astype('float32')
        blue,green,red=band[:,:,0],band[:,:,1],band[:,:,2]
        colored=((green>red*1.25)&(green>120)&(blue>red*1.15))|((red>green*1.25)&(red>140))
        if colored.mean()<.3: raise RuntimeError('No selected-answer feedback; paused')
        require_wechat(adb,s)
        adb.run(*s,'shell','input','tap',str(round(v['width']*.925)),str(counter['center'][1]))
        time.sleep(.6)
        _,next_view=read()
        next_counters=[e['text'] for e in next_view['elements'] if re.fullmatch(r'\d+/\d+',e['text']) and e['bounds'][1]>v['height']*.9]
        completed=any('卡包已完成' in e['text'] for e in next_view['elements'])
        if not completed and next_counters!=[f'{n+1}/{total}']: raise RuntimeError('Next page did not advance')
        adb.log.event('arbitrary_practice_choice',header=a.header,page=counter['text'],option='A',source='explicit_user_instruction',api_calls=0)
        print(json.dumps({'header':a.header,'answered_page':counter['text'],'next':next_counters,'completion':completed,'api_calls':0},ensure_ascii=False),flush=True)
    print('Bounded batch ended',flush=True)

if __name__=='__main__': main()
