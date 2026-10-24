"""Portable entry point; no paid calls or automatic answers from this menu."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent

def main():
    while True:
        print('\n翻转外语 USB 辅助工具（默认不调用付费 API、不自动答题）')
        print('1 检查 USB 连接\n2 截图并打开\n3 本地 OCR 读取当前页面\n4 只读投屏\n5 离线测试\n0 退出')
        try: choice=input('请选择：').strip()
        except (EOFError,KeyboardInterrupt): return 0
        if choice=='0': return 0
        commands={'1':['phone_capture.py','doctor'],'2':['phone_capture.py','capture','--open'],
                  '3':['assistant_step.py','--live'],'5':['-m','unittest','discover','-s','tests','-v']}
        if choice in commands:
            args=commands[choice]
            if args[0].endswith('.py'):args=[str(ROOT/args[0]),*args[1:]]
            subprocess.run([sys.executable,*args],cwd=ROOT,check=False)
        elif choice=='4':
            scrcpy=ROOT/'tools/scrcpy-win64-v5.0.1/scrcpy.exe'
            adb=ROOT/'tools/scrcpy-win64-v5.0.1/adb.exe'
            if not scrcpy.is_file():print('缺少 scrcpy；请使用完整 Windows 便携包。');continue
            env=dict(os.environ,ADB=str(adb))
            subprocess.Popen([str(scrcpy),'--select-usb','--no-audio','--no-clipboard-autosync',
                              '--no-control','--no-power-on','--window-title','Fanzhuan - Phone View'],
                             cwd=scrcpy.parent,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        else: print('请输入菜单中的数字。')

if __name__=='__main__':raise SystemExit(main())
