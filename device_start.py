"""Prepare the connected replacement phone once, then launch a bounded worker."""
import argparse
import json
import sys

from desktop_helper import current_session
from phone_capture import ROOT,Adb,RunLog,find_adb
from session_switch import switch_session,ensure_no_workers
from daily_word_tasks import run_card_worker

TASKS={'daily_tasks.py','daily_word_tasks.py','daily_words.py','daily_listening.py','daily_choice_pages.py','daily_spell.py'}


def authorized_source(root):
    folder=current_session(root)
    if folder is None:raise RuntimeError('请先在窗口新建已授权的会话；不会自动增加 API 额度。')
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if auth.get('status') not in {'active','completed'} or not auth.get('automatic_answers'):
        raise RuntimeError('当前会话没有可继续的自动答题授权，请在窗口新建会话。')
    return folder,auth


def prepare_session(root,adb):
    authorized_source(root)
    folder,changed=switch_session(root,adb,allow_completed=True)
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    if auth.get('status')=='completed':
        raise RuntimeError('当前手机的本次任务已完成。连接并授权另一台手机后再点“开始运行”，或为新的任务显式新建会话。')
    print('已自动识别更换手机，沿用剩余额度并建立独立记录。' if changed else '已核对当前 USB 手机与会话绑定。',flush=True)
    print('API 剩余 '+str(auth.get('max_new_deepseek_generation_calls',0)-auth.get('used_generation_calls',0))+' 次；未新增额度。',flush=True)
    RunLog(folder).event('device_start_prepared',replacement=changed,phone_actions=0,api_calls=0)
    return folder,changed


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepare-only',action='store_true',help='Check USB and bind a replacement without screen input or API calls')
    p.add_argument('task',nargs=argparse.REMAINDER)
    args=p.parse_args(argv)
    task=args.task
    if task and task[0]=='--':task=task[1:]
    if not args.prepare_only and (not task or task[0] not in TASKS):p.error('Expected an allowlisted task script after --')
    if args.prepare_only and task:p.error('prepare-only cannot start a task')
    folder,_=authorized_source(ROOT)
    ensure_no_workers(ROOT)
    adb=Adb(find_adb(None),20,RunLog(folder))
    prepare_session(ROOT,adb)
    if args.prepare_only:return 0
    try:
        # Explicit UTF-8 pipes are required for Windows hidden child processes.
        # The GUI's Stop button terminates this wrapper and the full child tree.
        code,_=run_card_worker([sys.executable,'-X','utf8','-u',str(ROOT/task[0]),*task[1:]],ROOT)
        return code
    except OSError as error:
        raise RuntimeError('设备检查完成，但任务脚本未能启动：'+str(error)) from error


if __name__=='__main__':
    try:sys.exit(main())
    except RuntimeError as error:
        print('启动未完成：'+str(error),file=sys.stderr,flush=True);sys.exit(2)
