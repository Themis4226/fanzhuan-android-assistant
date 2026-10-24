"""Create a fresh device-bound local session. No inherited permissions or keys."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from phone_capture import ROOT,Adb,RunLog,find_adb,parse_devices

def main():
    p=argparse.ArgumentParser(description='新建本地运行会话；默认不允许自动答题或付费调用')
    p.add_argument('--authorize-answers',action='store_true',help='明确授权本次会话自动答题及提交')
    p.add_argument('--allow-api',action='store_true',help='明确允许本次会话发送题目相关 OCR 文字到 DeepSeek')
    p.add_argument('--api-call-limit',type=int,default=0)
    p.add_argument('--arbitrary-choice-header',action='append',default=[],help='明确允许直接选择选项的页面标题')
    a=p.parse_args()
    if not 0<=a.api_call_limit<=100:p.error('API 次数范围为0..100')
    if a.api_call_limit and not (a.allow_api and a.authorize_answers):p.error('付费额度需同时显式指定 --allow-api 和 --authorize-answers')
    if a.arbitrary_choice_header and not a.authorize_answers:p.error('直接选择选项需要 --authorize-answers')
    active=ROOT/'runs/active_daily_session.txt'
    if active.is_file():
        previous=Path(active.read_text(encoding='utf-8').strip())/'authorization.json'
        if previous.is_file() and json.loads(previous.read_text(encoding='utf-8')).get('status')!='completed':
            raise RuntimeError('已有未结束的会话，请先检查现场，不覆盖其进度和额度。')
    folder=ROOT/'runs'/(datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_daily')
    folder.mkdir(parents=True)
    adb=Adb(find_adb(None),20,RunLog(folder))
    device=adb.select_usb(parse_devices(adb.text('devices','-l')),None)
    auth={'at':datetime.now().astimezone().isoformat(),'status':'active','scope':'Current device Fanzhuan practice',
          'automatic_answers':a.authorize_answers,'automatic_daily_submissions':a.authorize_answers,
          'max_new_deepseek_generation_calls':a.api_call_limit,'used_generation_calls':0,
          'phone_content_scope':'Task OCR only; no chats, credentials, or screenshots',
          'arbitrary_choice_headers':a.arbitrary_choice_header,'serial':device['serial']}
    (folder/'authorization.json').write_text(json.dumps(auth,ensure_ascii=False,indent=2),encoding='utf-8')
    active.write_text(str(folder),encoding='utf-8')
    print('已创建本地会话：',folder)
    print('自动答题：',a.authorize_answers,'；API 调用上限：',a.api_call_limit)

if __name__=='__main__':main()
