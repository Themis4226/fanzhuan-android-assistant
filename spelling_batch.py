"""Spelling from previously verified task definitions; ambiguous clues request review."""
import json
from pathlib import Path
import re

import cv2
from daily_words import chinese,canonical
from ocr_view import decode_image
from phone_capture import ROOT,RunLog
import daily_navigation as nav
from verified_vocabulary import spelling_bank
from daily_spell import spelling_clues


def spelling_complete(view,total=25):
    if not nav.has_header(view,'单词任务'):return False
    headings=[e for e in view['elements'] if e['text']=='单词拼写' and e.get('confidence',0)>=.9]
    if len(headings)!=1:return False
    y=headings[0]['center'][1]
    counts=[e for e in view['elements'] if e['text']==f'{total}/{total}' and e.get('confidence',0)>=.9
            and y<e['center'][1]<y+view['height']*.12]
    return (len(counts)==1 and any(e['text']=='已完成' and e['center'][0]>view['width']*.7
            and abs(e['center'][1]-counts[0]['center'][1])<view['height']*.04 for e in view['elements']))


def completion_after_last_answer(state,view):
    """The observed spelling completion has no course header or page counter."""
    n,total=map(int,state['progress'].split('/'))
    texts={e['text'] for e in view['elements'] if e.get('confidence',0)>=.9}
    buttons=[e for e in view['elements'] if e['text']=='完成' and e.get('confidence',0)>=.9
             and e['center'][1]>view['height']*.7]
    return n==total==state['total'] and 'COMPLETE' in texts and nav.card_completed(view) and len(buttons)==1


def context(raw,view):
    if any('温馨提示' in e['text'] for e in view['elements']):raise RuntimeError('拼写提示弹窗尚未关闭。')
    course=nav.header(view)['text'];h,w=view['height'],view['width']
    progress=[e['text'] for e in view['elements'] if re.fullmatch(r'\d+/\d+',e['text'])
              and e.get('confidence',0)>=.85 and h*.105<e['center'][1]<h*.18]
    clues=spelling_clues(view)
    if len(progress)!=1 or not clues:raise RuntimeError('拼写题意或进度不清晰。')
    n,total=map(int,progress[0].split('/'))
    if not 1<=n<=total<=30:raise RuntimeError('拼写题数超出已验证范围。')
    clue=''.join(clues)
    image=decode_image(raw)
    if image.shape[:2]!=(h,w):raise RuntimeError('截图与文字尺寸不符。')
    if any(re.search('[A-Za-z]',e['text']) for e in view['elements']
           if h*.35<e['center'][1]<h*.55 and w*.10<e['center'][0]<w*.87):
        raise RuntimeError('拼写输入区已有字母，保留现场，不追加输入。')
    roi=image[round(h*.35):round(h*.55),round(w*.10):round(w*.87)]
    mask=(cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY)<70).astype('uint8')
    _,_,stats,_=cv2.connectedComponentsWithStats(mask)
    slots=[(x,y,bw,bh,area) for x,y,bw,bh,area in stats[1:] if bw>w*.04 and bh<h*.006 and area>40]
    blanks=len(slots)
    if not 2<=blanks<=30:raise RuntimeError('字母空格不清晰或已存在部分输入，保留现场。')
    left=min(s[0] for s in slots);right=max(s[0]+s[2] for s in slots);baseline=min(s[1] for s in slots)
    if any(left<=x<=right and baseline-h*.08<y<baseline and bh>h*.007 and bw>w*.003 and area>20
           for x,y,bw,bh,area in stats[1:]):
        raise RuntimeError('拼写输入区已有笔画，保留现场，不追加输入。')
    return {'header':canonical(course),'clue':chinese(clue),'raw_clue':clue,'progress':progress[0],
            'length':blanks,'total':total}


def observed_bank(root):
    return spelling_bank(root)


def choose_word(state,bank,proposals=()):
    for item in reversed(list(proposals)):
        if item.get('source')!='assistant_review':continue
        if all(item.get(k)==state[k] for k in ('header','clue','progress','length')):
            word=item.get('word','')
            if not re.fullmatch('[A-Z]?[a-z]{1,29}',word) or len(word)!=state['length']:
                raise RuntimeError('复核的拼写建议不符合字母数。')
            return word,'assistant_review'
    candidates=sorted(word for word in bank.get(state['clue'],()) if len(word)==state['length'])
    if len(candidates)==1:return candidates[0],'previous_green_feedback_definition'
    return None,'ambiguous_or_unknown'


def run_batch(runtime,folder,*,root=ROOT,count=25,expected_header=None,expected_total=None):
    if not 1<=count<=30:raise ValueError('count must be 1..30')
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if auth.get('status')!='active' or not auth.get('automatic_answers'):raise RuntimeError('No active spelling authorization')
    log=RunLog(folder)
    bank=observed_bank(root);raw,v=runtime.current();course=None
    if expected_header and not nav.has_header(v,expected_header):
        raise RuntimeError('拼写标题与刚才选择的课程不一致。')
    if any(e['text']=='温馨提示' for e in v['elements']) and any('拼写完成后点击键盘' in e['text'] for e in v['elements']):
        buttons=[e for e in v['elements'] if e['text']=='确认' and e.get('confidence',0)>=.9
                 and v['height']*.4<e['center'][1]<v['height']*.75]
        if len(buttons)!=1:raise RuntimeError('拼写说明确认按钮不唯一。')
        runtime.guard();runtime.adb.run(*runtime.selector,'shell','input','tap',*(str(c) for c in buttons[0]['center']))
        raw,v=runtime.read()
    for step in range(count):
        state=context(raw,v)
        if course is None:course=state['header']
        if (state['header']!=course or (expected_header and state['header']!=canonical(expected_header))
                or (expected_total is not None and state['total']!=expected_total)):
            raise RuntimeError('拼写课程或总题数在批次中发生变化。')
        path=folder/'reviewed_spelling_proposals.jsonl'
        proposals=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.is_file() else []
        word,source=choose_word(state,bank,proposals)
        if word is None:
            (folder/'spelling_handoff.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            from spelling_suggest import suggest_spelling
            print('拼写'+state['progress']+'本地未匹配，检查原有模型额度…',flush=True)
            word,source=suggest_spelling(folder,state,log)
        print('拼写'+state['progress']+'：'+word+'；来源：'+source,flush=True)
        raw,after=runtime.answer(word,state['raw_clue'],state['progress'],expected_header=state['header'])
        if completion_after_last_answer(state,after):
            (folder/'spelling_completion.png').write_bytes(raw)
            (folder/'spelling_completion.json').write_text(json.dumps(after,ensure_ascii=False,indent=2),encoding='utf-8')
            log.event('spelling_completion_observed',progress=state['progress'],api_calls=0)
            print('最后一道拼写提交后已观察到任务完成页；返回列表核对。',flush=True)
            return raw,after,state
        next_state=context(raw,after)
        if (next_state['header']!=course or next_state['progress']!=str(int(state['progress'].split('/')[0])+1)+'/'+str(state['total'])):
            raise RuntimeError('拼写提交后进度或课程不符合预期。')
        log.event('spelling_batch_answer_verified',word=word,progress=state['progress'],source=source,api_calls=0)
        v=after
    raise RuntimeError('本次拼写步数已用完，保留当前进度。')


def main(count=25,*,no_cache=False):
    from daily_spell import create_runtime
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    return run_batch(create_runtime(folder,no_cache=no_cache),folder,count=count)
