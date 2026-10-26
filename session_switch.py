"""Explicitly switch phones without resetting fees or reusing phone progress."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from desktop_helper import current_session
from phone_capture import ROOT, Adb, RunLog, find_adb, parse_devices


AUTH_FIELDS = ('automatic_answers', 'automatic_daily_submissions',
               'max_new_deepseek_generation_calls', 'used_generation_calls',
               'prior_authorized_calls_used', 'shared_authorized_call_limit',
               'additional_call_authorizations', 'phone_content_scope',
               'authorization_source', 'practice_preference_source',
               'arbitrary_choice_categories', 'skip_study_pages')
WORKERS = ('device_start.py', 'daily_tasks.py', 'daily_word_tasks.py', 'daily_words.py',
           'daily_listening.py', 'daily_choice_pages.py', 'daily_spell.py',
           'daily_navigate.py', 'daily_suggest.py', 'assistant_step.py',
           'session_init.py', 'session_switch.py')


def worker_conflicts(records,root,pid,entry_script,parent_pid=None):
    """Ignore only our own venv bootstrap ancestors, not another controller."""
    lookup={p['ProcessId']:p for p in records}
    ignored={pid};parent=lookup.get(pid,{}).get('ParentProcessId',parent_pid)
    same_entry=re.compile(r'(?<![\w.])'+re.escape(Path(entry_script).name)+r'(?![\w.])',re.I)
    seen=set()
    while parent in lookup and parent not in seen:
        seen.add(parent);ancestor=lookup[parent]
        if same_entry.search(ancestor.get('CommandLine') or ''):ignored.add(parent)
        else:break
        parent=ancestor.get('ParentProcessId')
    projects={str(root.absolute()).replace('/','\\').casefold(),str(root.resolve()).replace('/','\\').casefold()}
    result=[]
    for process in records:
        if process['ProcessId'] in ignored:continue
        command=(process.get('CommandLine') or '').replace('/','\\').casefold()
        if any(p in command for p in projects) and any(name in command for name in WORKERS):result.append(process)
    return result


def ensure_no_workers(root):
    if os.name != 'nt':
        raise RuntimeError('更换手机入口目前仅适用于 Windows。')
    command = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
               "Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe') } "
               "| Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=15,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('无法检查其他任务进程，未更换手机。请关闭任务后重试。')
    records = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(records, dict):
        records = [records]
    if worker_conflicts(records,root,os.getpid(),sys.argv[0],os.getppid()):
        raise RuntimeError('另一个手机任务仍在运行；请先停止，再更换手机。')


def atomic_write(path, value):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False, suffix='.tmp') as stream:
        stream.write(value); temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def switch_session(root, adb, *, allow_completed=False):
    old_folder = current_session(root)
    if old_folder is None:
        raise RuntimeError('没有进行中的会话，请用“新建会话”绑定手机。')
    old_path = old_folder/'authorization.json'
    original = old_path.read_text(encoding='utf-8')
    previous = json.loads(original)
    allowed={'active','completed'} if allow_completed else {'active'}
    if previous.get('status') not in allowed or previous.get('budget_transferred_out'):
        raise RuntimeError('旧会话已结束，不能转移额度；请新建会话。')
    used = previous.get('used_generation_calls', 0)
    limit = previous.get('max_new_deepseek_generation_calls', 0)
    if type(used) is not int or type(limit) is not int or not 0 <= used <= limit:
        raise RuntimeError('原会话额度不合法，未更换手机。')
    device = adb.select_usb(parse_devices(adb.text('devices', '-l')), None)
    if device['serial'] == previous.get('serial'):
        return old_folder, False
    selector = adb.selector(device)
    model = adb.text(*selector, 'shell', 'getprop', 'ro.product.model')
    # Recheck USB transport after the metadata reads, before writing any authorization.
    adb.select_usb(parse_devices(adb.text('devices', '-l')), device['serial'])
    if old_path.read_text(encoding='utf-8') != original:
        raise RuntimeError('会话在更换期间已变化，未转移；请停止任务后重试。')
    now = datetime.now().astimezone()
    folder = root/'runs'/(now.strftime('%Y%m%d_%H%M%S_%f')+'_daily_device')
    folder.mkdir()
    auth = {key: previous[key] for key in AUTH_FIELDS if key in previous}
    auth.update(at=now.isoformat(), status='active', scope='Current device Fanzhuan practice',
                serial=device['serial'], device_model=model,
                arbitrary_choice_headers=[], budget_carried_from=old_folder.name,
                device_switch_source='Explicit user request to use the connected replacement phone')
    new_path = folder/'authorization.json'
    new_path.write_text(json.dumps(auth, ensure_ascii=False, indent=2), encoding='utf-8')
    closed=dict(previous,successor_session=folder.name,budget_transferred_out=True)
    if previous['status']=='active':closed.update(status='closed',closure_reason='device_switched')
    pointer = root/'runs/active_daily_session.txt'
    try:
        # Close the source before moving the pointer: two active budgets must never result.
        atomic_write(old_path, json.dumps(closed, ensure_ascii=False, indent=2))
        atomic_write(pointer, str(folder))
    except OSError:
        auth.update(status='closed', closure_reason='switch_failed')
        atomic_write(new_path, json.dumps(auth, ensure_ascii=False, indent=2))
        atomic_write(old_path, original)
        raise
    RunLog(old_folder).event('session_transferred_to_replacement_phone', successor_session=folder.name,
                            learning_completion_claimed=False, api_calls=0, phone_actions=0)
    RunLog(folder).event('replacement_phone_session_created', previous_session=old_folder.name,
                        budget_preserved=True, used_generation_calls=used, api_call_limit=limit,
                        phone_progress_inherited=False, api_calls=0, phone_actions=0)
    return folder, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--switch-device', action='store_true', required=True,
                        help='Explicitly bind the only authorized USB phone and carry the remaining budget')
    parser.parse_args(argv)
    ensure_no_workers(ROOT)
    folder = current_session(ROOT)
    if folder is None:
        raise RuntimeError('没有进行中的会话，请先新建会话。')
    adb = Adb(find_adb(None), 20, RunLog(folder))
    folder, changed = switch_session(ROOT, adb,allow_completed=True)
    auth = json.loads((folder/'authorization.json').read_text(encoding='utf-8'))
    print('已更换到当前 USB 手机，并建立独立运行记录。' if changed else '当前会话已经绑定这台手机，无需更换。', flush=True)
    print('API 剩余 '+str(auth.get('max_new_deepseek_generation_calls',0)-auth.get('used_generation_calls',0))+
          ' 次；已用次数和总额度均保留，未调用 API，未操作手机页面。', flush=True)


if __name__ == '__main__':
    main()
