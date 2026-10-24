"""Type one reviewed spelling answer using the observed in-app keyboard."""
import argparse
import json
import re
import time
import difflib
from pathlib import Path
import numpy as np
import cv2
from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices
from assistant_step import fresh_capture, require_wechat
from ocr_view import load_engine, observe, decode_image
from daily_words import chinese
from daily_privacy import assert_no_notification
from fast_observer import ExactFrameObserver


def type_letters(adb, selector, word, centers, width, height):
    """One USB shell round-trip per word; never include confirm or next-page.

    Only generated, checked integer coordinates enter the Android shell. The
    word itself is not shell text. A failing input command stops the sequence.
    """
    if not re.fullmatch('[a-z]{2,30}', word):
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

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--word',required=True)
    p.add_argument('--clue',required=True)
    p.add_argument('--progress',required=True)
    p.add_argument('--no-ocr-cache',action='store_true')
    a=p.parse_args()
    if not re.fullmatch('[a-z]{2,30}',a.word): p.error('Lowercase alphabetic word only')
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if not auth.get('automatic_answers') or auth.get('status')=='completed': raise RuntimeError('No active authorization')
    adb=Adb(find_adb(None),20,RunLog(folder));s=adb.selector(adb.select_usb(parse_devices(adb.text('devices','-l')),auth.get('serial')))
    engine=load_engine();engine.use_cls=False
    observer=ExactFrameObserver(engine,observe,log=adb.log,enabled=not a.no_ocr_cache)
    last_raw=None
    def read():
        nonlocal last_raw
        require_wechat(adb,s);raw=fresh_capture(adb,s);v=observer(raw,live=True)
        assert_no_notification(v)
        last_raw=raw
        (folder/'latest.png').write_bytes(raw);(folder/'latest.json').write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
        return v
    def clues(v):return [e['text'] for e in v['elements'] if 450<e['center'][1]<v['height']*.55 and chinese(e['text'])]
    v=read();body=clues(v)
    if chinese(a.clue) not in chinese(''.join(body)) or not any(e['text']==a.progress and 260<e['center'][1]<420 for e in v['elements']):
        raise RuntimeError('Spelling clue or progress changed')
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
    require_wechat(adb,s)
    type_letters(adb,s,a.word,centers,v['width'],v['height'])
    typed=read()
    if (difflib.SequenceMatcher(None,chinese(''.join(clues(typed))),chinese(''.join(body))).ratio()<.96
            or not any(e['text']==a.progress and 260<e['center'][1]<420 for e in typed['elements'])):
        raise RuntimeError('Question changed while typing')
    buttons=[e for e in typed['elements'] if e['text']=='确认' and e['confidence']>=.85 and e['center'][1]>v['height']*.9]
    if len(buttons)!=1: raise RuntimeError('Keyboard confirm not clearly visible')
    require_wechat(adb,s)
    adb.run(*s,'shell','input','tap',*(str(c) for c in buttons[0]['center']))
    time.sleep(.7);after=read()
    n,total=map(int,a.progress.split('/'))
    if not (any(e['text']==f'{n+1}/{total}' and 260<e['center'][1]<420 for e in after['elements'])
            or any('任务已完成' in e['text'] for e in after['elements'])):
        raise RuntimeError('Spelling submission did not advance; inspect actual feedback')
    adb.log.event('daily_spelling_answer',word=a.word,clue=body,progress=a.progress,api_calls=0,source='reviewed_OCR_answer')
    print(json.dumps({'submitted_word':a.word,'after':[e['text'] for e in after['elements'] if 100<e['center'][1]<after['height']*.65]},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
