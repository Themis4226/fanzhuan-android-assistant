"""Bounded, observed word-card review for the explicitly authorized daily run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time
import difflib

from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices, timestamp
from assistant_step import fresh_capture, require_wechat
from ocr_view import load_engine, observe
from ocr_view import decode_image
from model_control import build_request, call_deepseek, load_api_key, validate_decision
from daily_privacy import assert_no_notification
from fast_observer import ExactFrameObserver


def chinese(text):
    return ''.join(re.findall(r'[\u4e00-\u9fff]', text))


def canonical(text):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text.lower())


def question_signature(view):
    relevant=[]
    for e in view['elements']:
        text=e['text']
        if (re.match(r'^[ABCD][.、:．)]',text) or re.search(r'学习\s*\d+\s*/\s*\d+',text)
                or (e['bounds'][1]>view['height']*.28 and re.fullmatch(r'[A-Za-z][A-Za-z -]*',text))):
            relevant.append(canonical(text))
    return sorted(relevant)


def correct_rows(raw, options):
    image = decode_image(raw)
    results = []
    for option in options:
        _, y1, _, y2 = option['bounds']
        band = image[y1:y2, round(image.shape[1]*.12):round(image.shape[1]*.85)].astype('float32')
        blue, green, red = band[:,:,0], band[:,:,1], band[:,:,2]
        mask = (green > red*1.25) & (green > 120) & (blue > red*1.15)
        if mask.mean() > .35:
            results.append(option)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=5)
    parser.add_argument('--skip-review', action='store_true', help='Use normal upward swipes to bypass definition cards')
    parser.add_argument('--no-ocr-cache', action='store_true', help='Re-run OCR even for identical fresh screenshots')
    args = parser.parse_args()
    if not 1 <= args.count <= 50:
        parser.error('count must be 1..50')
    folder = Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8'))
    auth = json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if not auth.get('automatic_answers') or auth.get('status') in {'completed','closed'}:
        raise RuntimeError('Daily task authorization is missing')
    adb = Adb(find_adb(None), 20, RunLog(folder))
    selector = adb.selector(adb.select_usb(parse_devices(adb.text('devices', '-l')), auth.get('serial')))
    engine = load_engine()
    # This verified portrait UI is upright. Avoid rotating short Latin words
    # such as "soup" into the false OCR reading "dnos".
    engine.use_cls = False
    observer = ExactFrameObserver(engine, observe, log=adb.log, enabled=not args.no_ocr_cache)

    last_raw = None
    dictionary = {}
    vocabulary = folder/'agent_vocabulary.json'
    if vocabulary.is_file():
        dictionary.update(json.loads(vocabulary.read_text(encoding='utf-8')))
    for filename in ('word_reviews.jsonl','correct_word_answers.jsonl'):
        file = folder/filename
        if file.is_file():
            for line in file.read_text(encoding='utf-8').splitlines():
                item = json.loads(line)
                dictionary.setdefault(item['word'].lower(), []).extend(item.get('definition', []))
    original = folder/'after_word_tap.json'
    if original.is_file():
        original_view=json.loads(original.read_text(encoding='utf-8'))
        english=[e['text'] for e in original_view['elements'] if e['bounds'][1]>700 and re.fullmatch(r'[A-Za-z][A-Za-z -]*',e['text'])]
        meanings=[e['text'] for e in original_view['elements'] if e['bounds'][1]>700 and chinese(e['text'])]
        if len(english)==1 and meanings: dictionary[english[0].lower()]=meanings

    def read():
        nonlocal last_raw
        require_wechat(adb, selector)
        raw = fresh_capture(adb, selector)
        last_raw = raw
        view = observer(raw, live=True)
        assert_no_notification(view)
        (folder/'latest.png').write_bytes(raw)
        (folder/'latest.json').write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding='utf-8')
        return view

    def advance(view):
        require_wechat(adb, selector)
        width, height = view['width'], view['height']
        x, y1, y2 = round(width*.5), round(height*.75), round(height*.30)
        adb.run(*selector, 'shell', 'input', 'swipe', str(x), str(y1), str(x), str(y2), '600')
        time.sleep(.6)

    view = read()
    for _ in range(args.count):
        if not any('卡包' in e['text'] for e in view['elements']):
            print(json.dumps({'status':'stopped_page_changed'}, ensure_ascii=False), flush=True)
            return
        if any('恭喜你完成卡包' in e['text'] for e in view['elements']):
            print(json.dumps({'status':'card_completion_observed'},ensure_ascii=False),flush=True)
            return
        options = [e for e in view['elements'] if re.match(r'^[ABCD][.、:．)]', e['text'])]
        words = [e for e in view['elements'] if e['bounds'][1] > view['height']*.28
                 and re.fullmatch(r'[A-Za-z][A-Za-z -]*', e['text']) and e['confidence'] >= .85]
        if 1 < len(words) <= 3 and max(e['bounds'][3] for e in words)-min(e['bounds'][1] for e in words)<view['height']*.25:
            words.sort(key=lambda e:e['bounds'][1])
            words=[dict(words[0],text=' '.join(e['text'] for e in words))]
        if len(words) != 1:
            print(json.dumps({'status':'stopped_unexpected_card','texts':[e['text'] for e in view['elements'] if e['bounds'][1]>220]}, ensure_ascii=False), flush=True)
            return
        word = words[0]
        if len(options) >= 3:
            correct = correct_rows(last_raw, options)
            if not correct:
                meanings = dictionary.get(word['text'].lower(), [])
                meanings = meanings + [''.join(chinese(text) for text in meanings)]
                ranked = []
                for option in options:
                    score = max((difflib.SequenceMatcher(None,chinese(option['text']),chinese(meaning)).ratio()
                                 for meaning in meanings if chinese(meaning)), default=0)
                    ranked.append((score, option))
                ranked.sort(key=lambda value:value[0], reverse=True)
                if (ranked[0][0] >= .88 and ranked[0][0]-ranked[1][0] >= .12
                        and ranked[0][1]['confidence'] >= .85):
                    target = ranked[0][1]
                    reason = 'Matched previously observed actual card definition'
                    source = 'local_definition_match'
                else:
                    auth = json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
                    used = auth.get('used_generation_calls',0)
                    if used >= auth['max_new_deepseek_generation_calls']:
                        raise RuntimeError('Authorized API call limit reached; paused')
                    body_view = dict(view)
                    body_view['roi'] = [0,220,view['width'],view['height']-100]
                    body_view['elements'] = [e for e in view['elements'] if 220<=e['bounds'][1]<view['height']-100]
                    body = build_request(body_view)
                    body['messages'][0]['content'] += '\nVerified UI context: this word-card quiz shows an English term followed by four Chinese meanings. Select the meaning of that English term. The app does not print a separate question sentence; that is expected, not missing context.'
                    auth['used_generation_calls'] = used+1
                    (folder/'authorization.json').write_text(json.dumps(auth,ensure_ascii=False,indent=2),encoding='utf-8')
                    (folder/f'model_{used+1:03d}_request.json').write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8')
                    decision,usage = call_deepseek(body,load_api_key())
                    (folder/f'model_{used+1:03d}_decision.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8')
                    (folder/f'model_{used+1:03d}_usage.json').write_text(json.dumps(usage,ensure_ascii=False,indent=2),encoding='utf-8')
                    proposal=validate_decision(body_view,decision)
                    if proposal['action']!='tap':
                        raise RuntimeError('Model requested handoff: '+decision['reason'])
                    selected=[e for e in options if e['id']==proposal['target_id']]
                    if len(selected)!=1: raise RuntimeError('Model target is not an answer option')
                    target=selected[0]; reason=decision['reason']; source='deepseek'
                current = read()
                if question_signature(current) != question_signature(view):
                    raise RuntimeError('Question changed before tap; paused')
                live_targets=[e for e in current['elements'] if canonical(e['text'])==canonical(target['text']) and e['confidence']>=.85]
                if len(live_targets)!=1: raise RuntimeError('Live answer target is ambiguous; paused')
                target=live_targets[0]
                require_wechat(adb,selector)
                adb.run(*selector,'shell','input','tap',*(str(v) for v in target['center']))
                adb.log.event('automatic_daily_answer',word=word['text'],selected=target['text'],source=source,reason=reason)
                time.sleep(.65)
                view=read(); options=[e for e in view['elements'] if re.match(r'^[ABCD][.、:．)]',e['text'])]
                correct=correct_rows(last_raw,options)
                if not correct: raise RuntimeError('Answer feedback could not be verified; paused')
            if len(correct)!=1: raise RuntimeError('Correct answer feedback is ambiguous; paused')
            record={'at':timestamp(),'word':word['text'],'definition':[correct[0]['text']],
                    'progress':[e['text'] for e in view['elements'] if '学习' in e['text'] and '/' in e['text']],
                    'feedback':'green correct answer row observed'}
            dictionary[word['text'].lower()]=record['definition']
            with (folder/'correct_word_answers.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(record,ensure_ascii=False)+'\n')
            print(json.dumps(record,ensure_ascii=False),flush=True)
            advance(view)
            after = read()
            if question_signature(after) == question_signature(view):
                raise RuntimeError('Answered card did not advance; paused')
            view = after
            continue
        if args.skip_review:
            advance(view)
            adb.log.event('skipped_definition_card',word=word['text'])
            print(json.dumps({'status':'skipped_definition_card','word':word['text']},ensure_ascii=False),flush=True)
            after=read()
            if question_signature(after)==question_signature(view):
                raise RuntimeError('Skipped card did not advance; paused')
            view = after
            continue
        # Reveal definitions only when the optional review flow is selected.
        body = [e['text'] for e in view['elements'] if e['bounds'][1] > view['height']*.35]
        meaning = [text for text in body if len(chinese(text))>=2]
        if not meaning:
            require_wechat(adb, selector)
            adb.run(*selector, 'shell', 'input', 'tap', *(str(v) for v in word['center']))
            time.sleep(.5)
            view = read()
            meaning = [e['text'] for e in view['elements'] if e['bounds'][1] > view['height']*.35
                       and len(chinese(e['text']))>=2]
        if not meaning:
            print(json.dumps({'status':'stopped_definition_not_found','word':word['text']}, ensure_ascii=False), flush=True)
            return
        record = {'at':timestamp(), 'word':word['text'], 'definition':meaning,
                  'progress':[e['text'] for e in view['elements'] if '学习' in e['text'] and '/' in e['text']],
                  'api_calls':0, 'scope':'Actual card and definition read, not a completion claim'}
        with (folder/'word_reviews.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False)+'\n')
        print(json.dumps(record, ensure_ascii=False), flush=True)
        dictionary[word['text'].lower()]=meaning
        time.sleep(1.2)
        advance(view)
        adb.log.event('reviewed_card_swipe_up', word=word['text'], definition=meaning)
        after = read()
        next_words = [e['text'] for e in after['elements'] if e['bounds'][1] > after['height']*.28
                      and re.fullmatch(r'[A-Za-z][A-Za-z -]*', e['text'])]
        if word['text'] in next_words and not any(re.match(r'^[ABCD][.、:．)]',e['text']) for e in after['elements']):
            print(json.dumps({'status':'stopped_card_did_not_advance','word':word['text']}, ensure_ascii=False), flush=True)
            return
        view = after
    final=view
    print(json.dumps({'status':'batch_finished','count':args.count,
        'current_text':[e['text'] for e in final['elements'] if e['bounds'][1]>220]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
