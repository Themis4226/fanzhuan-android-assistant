"""One budgeted OCR-only model answer under this daily-session authorization."""
import json
import time
import re
from pathlib import Path
from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices
from assistant_step import fresh_capture, require_wechat
from ocr_view import load_engine, observe, decode_image
from model_control import build_request, call_deepseek, load_api_key, validate_decision
from daily_words import canonical
from daily_privacy import assert_no_notification

def validate_answer_text(target, decision):
    match=re.match(r'^Answer=("(?:[^"\\]|\\.)*");',decision['reason'])
    if not match or json.loads(match.group(1))!=target['text']:
        raise RuntimeError('Model answer text and target ID disagree; no tap performed')

def main():
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if not auth.get('automatic_answers') or auth.get('status') in {'completed','closed'}: raise RuntimeError('No active authorization')
    used=auth.get('used_generation_calls',0)
    if used>=auth['max_new_deepseek_generation_calls']: raise RuntimeError('API budget exhausted')
    adb=Adb(find_adb(None),20,RunLog(folder))
    s=adb.selector(adb.select_usb(parse_devices(adb.text('devices','-l')),auth.get('serial')))
    engine=load_engine(); engine.use_cls=False
    def read():
        require_wechat(adb,s)
        raw=fresh_capture(adb,s)
        v=observe(raw,engine=engine,live=True)
        assert_no_notification(v)
        (folder/'latest.png').write_bytes(raw)
        (folder/'latest.json').write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
        return raw,v
    def body(v):
        return [e for e in v['elements'] if 260<e['bounds'][1]<v['height']-100]
    raw,v=read()
    bv=dict(v,elements=body(v),roi=[0,260,v['width'],v['height']-100])
    request=build_request(bv)
    request['messages'][0]['content']+='\nThe user has authorized answering and submitting this daily practice. Solve the visible multiple-choice question and select its answer text region. Do not select a page arrow, submit button, unrelated header, or option letter alone. If required audio or passage is missing, handoff. For a tap, reason MUST start with Answer= followed by a JSON string containing the EXACT answer-region text, then a semicolon and your explanation. Example: Answer="The; the"; both refer to specific dogs. Double check that target_id is the ID of that exact answer text, not a nearby option letter or another row.'
    auth['used_generation_calls']=used+1
    (folder/'authorization.json').write_text(json.dumps(auth,ensure_ascii=False,indent=2),encoding='utf-8')
    prefix=f'model_{used+1:03d}'
    (folder/(prefix+'_request.json')).write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
    decision,usage=call_deepseek(request,load_api_key())
    (folder/(prefix+'_decision.json')).write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/(prefix+'_usage.json')).write_text(json.dumps(usage,ensure_ascii=False,indent=2),encoding='utf-8')
    proposal=validate_decision(bv,decision)
    if proposal['action']!='tap': raise RuntimeError('Model handoff: '+decision['reason'])
    target=next(e for e in bv['elements'] if e['id']==proposal['target_id'])
    validate_answer_text(target,decision)
    if target['bounds'][1]>v['height']*.9 or target['text'].strip() in {'A','B','C','D'}: raise RuntimeError('Target is not answer text')
    _,current=read()
    if sorted(canonical(e['text']) for e in body(current))!=sorted(canonical(e['text']) for e in body(v)):
        raise RuntimeError('Question changed before execution')
    live=[e for e in body(current) if canonical(e['text'])==canonical(target['text']) and e['confidence']>=.85]
    if len(live)!=1: raise RuntimeError('Answer text ambiguous')
    require_wechat(adb,s)
    adb.run(*s,'shell','input','tap',*(str(c) for c in live[0]['center']))
    adb.log.event('daily_model_answer_tap',selected=target['text'],reason=decision['reason'],api_call=used+1)
    time.sleep(.8)
    raw,after=read()
    (folder/'last_answer.png').write_bytes(raw)
    (folder/'last_answer.json').write_text(json.dumps(after,ensure_ascii=False,indent=2),encoding='utf-8')
    pixels=decode_image(raw)
    cy=live[0]['center'][1]; radius=round(v['width']*.04)
    band=pixels[max(0,cy-radius):cy+radius,round(v['width']*.025):round(v['width']*.12)].astype('float32')
    blue,green,red=band[:,:,0],band[:,:,1],band[:,:,2]
    selected=bool(((green>red*1.25)&(green>120)&(blue>red*1.15)).mean()>.35)
    print(json.dumps({'answer':target['text'],'reason':decision['reason'],'calls_used':used+1,'selection_indicator_verified':selected,'after':[e['text'] for e in body(after)],'note':'Selected answer indicator is not a correctness or task-completion claim'},ensure_ascii=False),flush=True)
    if not selected: raise RuntimeError('Selection indicator not verified; inspect before navigating')

if __name__=='__main__': main()
