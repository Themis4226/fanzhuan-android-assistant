"""Validate a moved bundle without calling paid APIs or controlling a phone."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

def main():
    p=argparse.ArgumentParser();p.add_argument('bundle',type=Path);p.add_argument('work',type=Path);p.add_argument('--reuse-work',action='store_true');a=p.parse_args()
    bundle=a.bundle.resolve();work=a.work.resolve()
    if a.reuse_work:
        if not work.is_relative_to(bundle.parents[1]) or not (work/'runtime/python.exe').is_file():raise RuntimeError('Not a prior verification copy')
    else:shutil.copytree(bundle,work,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    runtime=work/'runtime';python=runtime/'python.exe'
    # This exists only in the disposable moved copy, never the shipping tree.
    (runtime/'Lib/site-packages/sitecustomize.py').write_text("import socket\ndef _blocked(*a,**k): raise RuntimeError('Network disabled during package verification')\nsocket.create_connection=_blocked\nsocket.socket.connect=_blocked\n",encoding='utf-8')
    env=dict(os.environ,PYTHONUTF8='1');env.pop('DEEPSEEK_API_KEY',None);env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    checks=[]
    def run(name,args,input=None):
        result=subprocess.run(args,cwd=work,env=env,input=input,text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=120)
        if result.returncode:
            print(result.stdout);print(result.stderr);raise RuntimeError('Portable verification failed: '+name)
        checks.append({'name':name,'passed':True})
        print(name,'passed',flush=True)
        return result
    info=run('isolated_runtime_and_imports',[str(python),'-X','utf8','-c',
        "import sys,json,numpy,cv2,onnxruntime,rapidocr,PIL,shapely;from pathlib import Path;r=Path(sys.executable).parent;mods=[numpy,cv2,onnxruntime,rapidocr,PIL,shapely];assert all(Path(m.__file__).is_relative_to(r) for m in mods);assert sys.flags.isolated;print(json.dumps({'python':sys.version.split()[0],'numpy':numpy.__version__,'opencv':cv2.__version__,'onnxruntime':onnxruntime.__version__,'relocated_imports':True}))"])
    tests=run('offline_regression_tests',[str(python),'-X','utf8','-m','unittest','discover','-s','tests','-q'])
    demo=run('synthetic_local_ocr',[str(python),'-X','utf8','assistant_step.py','--demo','--keep-image'])
    reports=list((work/'runs').glob('*/report.json'))
    assert reports,'OCR report missing'
    report=json.loads(max(reports,key=lambda p:p.stat().st_mtime).read_text(encoding='utf-8'))
    assert report.get('status')=='ocr_ready_api_disabled',report.get('status')
    assert report.get('api_calls')==0 and not report.get('actions_performed')
    run('safe_launcher_exit',[str(python),'-X','utf8','launcher.py'],input='0\n')
    run('session_help',[str(python),'-X','utf8','session_init.py','--help'])
    adb=run('bundled_adb',[str(work/'tools/scrcpy-win64-v5.0.1/adb.exe'),'version'])
    assert 'Android Debug Bridge version' in adb.stdout
    scrcpy=run('bundled_scrcpy',[str(work/'tools/scrcpy-win64-v5.0.1/scrcpy.exe'),'--version'])
    assert 'scrcpy 5.0.1' in scrcpy.stdout+scrcpy.stderr
    run('portable_cmd_wrapper',f'cmd.exe /d /s /c ""{work / "python.cmd"}" --version"')
    desktop_test=work/'runs/desktop_validation'
    run('windowed_launcher_selftest',[str(work/'翻转外语助手.exe'),'--self-test',str(desktop_test)])
    desktop=json.loads((desktop_test/'desktop_validation.json').read_text(encoding='utf-8'))
    assert desktop['status']=='passed' and desktop['api_calls']==0 and desktop['phone_actions']==0
    result={'status':'passed','verified_at_utc':datetime.now(timezone.utc).isoformat(),'checks':checks,
            'versions':json.loads(info.stdout),'test_summary':tests.stderr.strip(),'desktop_validation':desktop,
            'api_calls':0,'phone_control_actions':0,'python_network_blocked':True,
            'relocated_under_unicode_and_spaces':True,'new_clean_windows_machine_verified':False,
            'limitations':'Verified on the current Windows computer after copying to a new Unicode/spaces path. Target USB drivers and system VC runtime depend on the target PC.'}
    for root in (bundle,a.bundle.parents[1]/'source/fanzhuan-android-assistant'):
        if root.is_dir():(root/'PACKAGE_VALIDATION.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
