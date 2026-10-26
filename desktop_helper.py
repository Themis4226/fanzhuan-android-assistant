"""Local desktop session metadata; no USB or model requests."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from phone_capture import ROOT, RunLog


def current_session(root=ROOT):
    pointer=root/'runs/active_daily_session.txt'
    if not pointer.is_file():return None
    folder=Path(pointer.read_text(encoding='utf-8').strip()).resolve()
    if not folder.is_relative_to((root/'runs').resolve()):
        raise RuntimeError('会话路径不属于当前项目，请核对项目目录。')
    if not (folder/'authorization.json').is_file():return None
    return folder


def status(root=ROOT):
    folder=current_session(root)
    if folder is None:return {'status':'none','authorized':False,'remaining':0,'api_used':0,'api_limit':0}
    auth=json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    used=int(auth.get('used_generation_calls',0));limit=int(auth.get('max_new_deepseek_generation_calls',0))
    return {'status':auth.get('status','unknown'),'authorized':bool(auth.get('automatic_answers')),
            'remaining':max(0,limit-used),'api_used':used,'api_limit':limit,
            'can_switch_device':auth.get('status') in {'active','completed'} and bool(auth.get('automatic_answers'))
                                and not auth.get('budget_transferred_out',False)}


def close_session(root=ROOT):
    folder=current_session(root)
    if folder is None:raise RuntimeError('没有可以结束的本地会话。')
    path=folder/'authorization.json';auth=json.loads(path.read_text(encoding='utf-8'))
    if auth.get('status') in {'completed','closed'}:return
    # This is an administrative closure, never a claim about learning progress.
    auth['status']='closed'
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=folder,delete=False,suffix='.tmp') as f:
        json.dump(auth,f,ensure_ascii=False,indent=2);temp=Path(f.name)
    os.replace(temp,path)
    RunLog(folder).event('local_session_closed',learning_completion_claimed=False,
                         used_generation_calls=auth.get('used_generation_calls',0))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--status',action='store_true')
    group.add_argument('--close-session',action='store_true')
    group.add_argument('--echo-args',nargs=argparse.REMAINDER,help=argparse.SUPPRESS)
    a=p.parse_args()
    if a.echo_args is not None:print(json.dumps(a.echo_args,ensure_ascii=False));return
    if a.close_session:close_session();print('本地会话已结束，原记录和调用次数已保留；手机学习进度未修改。')
    else:print(json.dumps(status(),ensure_ascii=False))


if __name__=='__main__':main()
