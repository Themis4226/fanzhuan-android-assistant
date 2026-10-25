"""Bounded word-card chain: complete -> list -> next pending card -> dashboard."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from phone_capture import ROOT,Adb,RunLog,find_adb,parse_devices
from assistant_step import require_wechat,fresh_capture
from ocr_view import load_engine,observe
from fast_observer import ExactFrameObserver
from daily_privacy import assert_no_notification
from daily_words import question_signature
from word_navigation import (active_card_id,is_completion,is_word_list,return_to_word_list,
                             open_pending_card,task_tiles,return_to_dashboard,dashboard_word_progress,
                             is_active_card,return_active_to_list,canonical)


def card_header(view):
    return sorted(canonical(e['text']) for e in view['elements']
                  if '卡包' in e['text'] and e['center'][1]<view['height']*.12)


def resume_matches(prior,view):
    return (prior.get('status')=='paused' and type(prior.get('resume_card_id')) is int
            and prior.get('resume_signature')==question_signature(view)
            and prior.get('resume_header')==card_header(view) and bool(card_header(view)))


def run_card_worker(command,cwd):
    """Forward nested worker output explicitly, including under a windowed parent."""
    last_line=''
    with subprocess.Popen(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          text=True,encoding='utf-8',errors='replace',bufsize=1,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
        for line in process.stdout:
            print(line,end='',flush=True)
            if line.strip():last_line=line.strip()
        return process.wait(),last_line


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--count',type=int,default=50,help='Maximum card steps per card')
    p.add_argument('--max-cards',type=int,default=6)
    p.add_argument('--navigation-only',action='store_true',help='Return/open one pending card, no answers or model calls')
    a=p.parse_args(argv)
    if not 1<=a.count<=50 or not 1<=a.max_cards<=10:p.error('count 1..50; max-cards 1..10')
    folder=Path((ROOT/'runs/active_daily_session.txt').read_text(encoding='utf-8').strip())
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if not auth.get('automatic_answers') or auth.get('status') in {'completed','closed'}:raise RuntimeError('没有进行中的自动任务授权。')
    log=RunLog(folder);adb=Adb(find_adb(None),20,log)
    selector=adb.selector(adb.select_usb(parse_devices(adb.text('devices','-l')),auth.get('serial')))
    engine=load_engine();engine.use_cls=False;observer=ExactFrameObserver(engine,observe,log=log)
    raw=None
    def read():
        nonlocal raw
        require_wechat(adb,selector);raw=fresh_capture(adb,selector);v=observer(raw,live=True);assert_no_notification(v)
        (folder/'latest.png').write_bytes(raw)
        (folder/'latest.json').write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
        return v
    def tap(center):
        require_wechat(adb,selector)
        if len(center)!=2 or any(type(x) is not int for x in center):raise RuntimeError('点击位置不是整数坐标。')
        adb.run(*selector,'shell','input','tap',*(str(x) for x in center))
    report_path=folder/'word_task_chain_result.json'
    prior=json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
    report={'completed_cards':[],'opened_cards':[],'navigation_only':a.navigation_only,'status':'running'}
    def save():
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    try:
        view=read();attempted=[];resume_id=None
        if is_completion(view):view=return_to_word_list(view,read,tap,log)
        if is_active_card(view) and active_card_id(view) is None:
            if resume_matches(prior,view):
                resume_id=prior['resume_card_id'];report['resumed_card']=resume_id;save()
                log.event('word_paused_card_resumed',card_id=resume_id,api_calls=0)
            else:view=return_active_to_list(view,read,tap,log)
        for _ in range(a.max_cards):
            if is_word_list(view):
                card_id,view=open_pending_card(read,lambda:raw,tap,log,attempted)
                if card_id is None:
                    view=return_to_dashboard(view,read,tap,log)
                    progress=dashboard_word_progress(view)
                    report['word_progress']=progress
                    report['status']=('word_tasks_completed_returned_to_dashboard' if progress and progress[0]==progress[1]
                                      else 'returned_to_dashboard_remaining_word_tasks_need_review')
                    save()
                    print(json.dumps(report,ensure_ascii=False),flush=True);return
                report['opened_cards'].append(card_id);save()
                if a.navigation_only:
                    report['status']='next_pending_card_opened_no_answers';save();print(json.dumps(report,ensure_ascii=False),flush=True);return
            else:
                card_id=active_card_id(view) or resume_id
                resume_id=None
                if card_id is None:raise RuntimeError('不是已确认的单词卡包、完成页或任务列表。')
                if a.navigation_only:
                    report['status']='already_inside_card_no_answers';save();print(json.dumps(report,ensure_ascii=False),flush=True);return
            attempted.append(card_id)
            log.event('word_card_worker_started',card_id=card_id,step_limit=a.count)
            print('开始卡包'+str(card_id)+'；沿用本次会话原有额度。',flush=True)
            code,last_line=run_card_worker([sys.executable,'-X','utf8','-u',str(ROOT/'daily_words.py'),
                                           '--skip-review','--count',str(a.count)],ROOT)
            if code:
                latest_auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
                if latest_auth.get('used_generation_calls',0)>=latest_auth['max_new_deepseek_generation_calls']:
                    raise RuntimeError('授权的模型调用次数已用完，已暂停；需要新增授权后才能继续。')
                raise RuntimeError('单词脚本已暂停：'+last_line+'；保留页面，不自动重试或跳过。')
            view=read()
            if is_completion(view):view=return_to_word_list(view,read,tap,log)
            if not is_word_list(view):raise RuntimeError('本批次未完成卡包，停止衔接下一项。可核对后继续当前卡包。')
            tiles=task_tiles(raw,view)
            verified=[t for t in tiles if t['card_id']==card_id and t['completed']]
            if len(verified)!=1:raise RuntimeError('列表未证实本卡包已完成，不会重放或跳到下一项。')
            report['completed_cards'].append(card_id);save()
            print('卡包'+str(card_id)+'完成并已返回列表。',flush=True)
        report['status']='card_limit_reached';save();print(json.dumps(report,ensure_ascii=False),flush=True)
    except Exception as e:
        report.update(status='paused',reason=str(e))
        if 'card_id' in locals() and card_id is not None and (folder/'latest.json').is_file():
            current=json.loads((folder/'latest.json').read_text(encoding='utf-8'))
            if is_active_card(current):report.update(resume_card_id=card_id,resume_signature=question_signature(current),
                                                    resume_header=card_header(current))
        save();raise


if __name__=='__main__':main()
