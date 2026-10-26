"""Build an allowlisted source tree and relocatable Windows x64 distribution."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import re
import shutil
import site
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='v0.2.2'
ARTIFACTS=[
 ('python-3.13.13-embed-amd64.zip','https://www.python.org/ftp/python/3.13.13/python-3.13.13-embeddable-amd64.zip','142666a4a9079507815d395b9bfb73546ec391003d385beb559a9d68fb240062'),
 ('scrcpy-win64-v5.0.1.zip','https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/scrcpy-win64-v5.0.1.zip','b12a2c4ee8be317422451fc7dcf8ee20a71b5ea7ef9ad73ddd825a25316227a5'),
 ('ffmpeg-9.0.2.tar.xz','https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz','8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e'),
 ('libusb-1.0.30.tar.gz','https://github.com/libusb/libusb/archive/refs/tags/v1.0.30.tar.gz','2ae28adb0bb9558c86135c4e1c11b320b0805461e207a64a6e520a114094bf07'),
 ('dav1d-1.5.4.tar.xz','https://downloads.videolan.org/pub/videolan/dav1d/1.5.4/dav1d-1.5.4.tar.xz','686616b7c69eb88d44459391ab25cac13b6647a3b288835c5784e71c1514a5c5'),
 ('sdl-3.4.18.tar.gz','https://github.com/libsdl-org/SDL/archive/refs/tags/release-3.4.18.tar.gz','c4b08b950bd29d83caae0ab8d884298d27046a9d30700fb42841c50992665c90'),
 ('geos-3.14.1.tar.bz2','https://download.osgeo.org/geos/geos-3.14.1.tar.bz2','3c20919cda9a505db07b5216baa980bacdaa0702da715b43f176fb07eff7e716'),
]

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def fetch(cache,item):
    name,url,sha=item;p=cache/name
    original=ROOT/'tools/downloads'/name
    if not p.exists() and original.is_file():shutil.copy2(original,p)
    if not p.exists():
        req=urllib.request.Request(url,headers={'User-Agent':'fanzhuan-portable-builder'})
        with urllib.request.urlopen(req,timeout=90) as r,p.open('xb') as f:shutil.copyfileobj(r,f)
    if digest(p)!=sha:raise RuntimeError('Artifact checksum mismatch: '+name)
    return {'name':name,'url':url,'sha256':sha,'bytes':p.stat().st_size}

def safe_extract(archive,target):
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not (target/name).resolve().is_relative_to(target.resolve()):raise RuntimeError('Unsafe archive member')
        z.extractall(target)

def copy_tree(source,dest):
    shutil.copytree(source,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc','direct_url.json'))

def archive_tree(folder,path):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(folder.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(folder.parent).as_posix())

def private_values():
    values=[]
    env=ROOT/'.env.local'
    if env.is_file():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('DEEPSEEK_API_KEY='):
                key=line.split('=',1)[1].strip().strip('\"\'')
                if key:values.append(key.encode())
    if os.environ.get('DEEPSEEK_API_KEY'):values.append(os.environ['DEEPSEEK_API_KEY'].encode())
    return values

def audit(folder):
    private=private_values();count=0
    for p in folder.rglob('*'):
        if not p.is_file():continue
        rel=p.relative_to(folder)
        if any(part in {'.venv','runs','.git','__pycache__'} for part in rel.parts) or p.name in {'.env.local','adbkey','adbkey.pub'}:
            raise RuntimeError('Forbidden release path: '+str(rel))
        if p.is_symlink():raise RuntimeError('Release symlink is forbidden')
        overlap=b''
        with p.open('rb') as f:
            while chunk:=f.read(1024*1024):
                data=overlap+chunk
                if any(value in data for value in private):raise RuntimeError('Private credential found in release file')
                overlap=data[-4096:]
        if rel.suffix in {'.py','.cmd','.cs','.md','.json','.txt','.yml','.yaml'} and rel.parts[0] not in {'runtime','third_party'}:
            content=p.read_bytes().lower()
            developer_name=Path.home().name.casefold().encode()
            if len(developer_name)>4 and developer_name in content:raise RuntimeError('Private development path found: '+str(rel))
        count+=1
    return count

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);parser.add_argument('--site-packages',type=Path)
    args=parser.parse_args()
    output=(args.output or ROOT/'dist'/datetime.now().strftime('%Y%m%d_%H%M%S')).resolve()
    output.mkdir(parents=True,exist_ok=False)
    desktop_binary=output/'desktop-launcher.exe'
    desktop=subprocess.run([sys.executable,str(ROOT/'packaging/build_desktop.py'),'--output',str(desktop_binary)],capture_output=True,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if desktop.returncode:raise RuntimeError('Desktop build failed: '+desktop.stdout.decode('utf-8',errors='replace')+desktop.stderr.decode('utf-8',errors='replace'))
    cache=ROOT/'packaging_cache';cache.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:artifacts=list(pool.map(lambda item:fetch(cache,item),ARTIFACTS))
    source=output/'source/fanzhuan-android-assistant';source.mkdir(parents=True)
    for pattern in ('*.py','*.cmd'):
        for p in ROOT.glob(pattern):shutil.copy2(p,source/p.name)
    for name in ('requirements.txt','requirements-ocr.txt','requirements-ocr.lock.txt','course_catalog.json'):shutil.copy2(ROOT/name,source/name)
    copy_tree(ROOT/'tests',source/'tests');copy_tree(ROOT/'packaging',source/'packaging')
    copy_tree(ROOT/'desktop',source/'desktop')
    shutil.copy2(desktop_binary,source/'翻转外语助手.exe')
    # The working guide contains local validation/session notes. Ship only the
    # public guide, never a user's device history, budgets or run references.
    shutil.copy2(ROOT/'packaging/GUI_USAGE.release.md',source/'GUI_USAGE.md')
    shutil.copy2(ROOT/'packaging/README.release.md',source/'README.md')
    (source/'.env.example').write_text('# Optional; paid calls are disabled by default.\nDEEPSEEK_API_KEY=\n',encoding='utf-8')
    (source/'.gitignore').write_text('.venv/\nruntime/\nruns/\n.env*\n!.env.example\n__pycache__/\n*.pyc\ntools/scrcpy*/\ntools/platform-tools/\ntools/downloads/\npackaging_cache/\ndist/\nthird_party/sources/\n*.key\nadbkey*\n',encoding='utf-8')
    licenses=source/'third_party/licenses';licenses.mkdir(parents=True)
    inventory=[]
    installed=args.site_packages or Path(site.getsitepackages()[-1])
    if not (installed/'rapidocr').is_dir():raise RuntimeError('Build with the project OCR environment')
    for d in sorted(distributions(path=[str(installed)]),key=lambda x:x.metadata.get('Name','')):
        name=d.metadata['Name']
        if name.lower()=='pip':continue
        copied=[]
        for f in d.files or []:
            if any(s in f.name.lower() for s in ('license','licence','notice','copying','authors')) and str(f).lower().endswith(('.txt','.md','license','licence','copying','authors')):
                real=d.locate_file(f)
                if real.is_file():
                    dest=licenses/name/str(f).replace('..','_');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(real,dest);copied.append(dest.relative_to(source).as_posix())
        inventory.append({'name':name,'version':d.version,'license_expression':d.metadata.get('License-Expression'),'license_files':copied})
    flat_license=cache/'flatbuffers-LICENSE.txt'
    if not flat_license.is_file():
        with urllib.request.urlopen('https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE',timeout=30) as r:flat_license.write_bytes(r.read())
    flat_dest=licenses/'flatbuffers/LICENSE.txt';flat_dest.parent.mkdir(exist_ok=True);shutil.copy2(flat_license,flat_dest)
    for component in inventory:
        if component['name'].lower()=='flatbuffers':component['license_files']=[flat_dest.relative_to(source).as_posix()]
    for name,_,_ in ARTIFACTS[2:]:
        with tarfile.open(cache/name) as tar:
            for m in tar.getmembers():
                if m.isfile() and len(Path(m.name).parts)<=2 and any(k in Path(m.name).name.lower() for k in ('license','copying','copyright')):
                    dest=licenses/Path(m.name).parts[0]/Path(m.name).name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(tar.extractfile(m).read())
    notice=ROOT/'tools/platform-tools/NOTICE.txt'
    if notice.is_file():shutil.copy2(notice,licenses/'ADB-NOTICE.txt')
    else:
        item=('platform-tools-37.0.1.zip','https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip','45f4d63113e895ebde0c90f194099a4676b6ac653bd28d54314a9e022bbc1a99')
        fetch(cache,item)
        with zipfile.ZipFile(cache/item[0]) as z:(licenses/'ADB-NOTICE.txt').write_bytes(z.read('platform-tools/NOTICE.txt'))
    (source/'DEPENDENCIES.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    notices='''# Third-party notices\n\nPython: PSF license, runtime/LICENSE.txt.\nRapidOCR and bundled PaddleOCR-derived weights: Apache-2.0, see runtime/Lib/site-packages/rapidocr*dist-info and MODEL_LICENSES.md.\nscrcpy: Apache-2.0, tools/scrcpy-win64-v5.0.1/LICENSE.txt. ADB notices: third_party/licenses/ADB-NOTICE.txt.\nFFmpeg 9.0.2: LGPL-2.1-or-later, shared DLLs, no GPL/nonfree configure options enabled in the upstream Windows build.\nlibusb 1.0.30: LGPL-2.1-or-later, shared DLL. dav1d 1.5.4: BSD-2-Clause. SDL 3.4.18: zlib.\nCorresponding source archives, full license texts and unmodified scrcpy dependency build scripts are included under third_party/. DLL replacement and reverse engineering for debugging modifications of LGPL components are not prohibited by this project.\nOther Python wheel components, including OpenCV/FFmpeg and Shapely/GEOS, retain their bundled license notices under runtime/Lib/site-packages and third_party/licenses; versions are listed in DEPENDENCIES.json.\nNo Microsoft fonts are redistributed; the app uses a locally installed Windows font when available.\nProject-authored source has not been assigned an open-source license.\n'''
    notices+='\nGEOS 3.14.1: LGPL-2.1-or-later; corresponding source is included in third_party/sources. The optional OpenCV video-file FFmpeg DLL is omitted; image decoding remains enabled.\n'
    (source/'THIRD_PARTY_NOTICES.md').write_text(notices,encoding='utf-8')
    portable=output/'portable/fanzhuan-android-assistant';copy_tree(source,portable)
    runtime=portable/'runtime';runtime.mkdir();safe_extract(cache/ARTIFACTS[0][0],runtime)
    (runtime/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n..\nimport site\n',encoding='ascii')
    target=runtime/'Lib/site-packages';target.mkdir(parents=True)
    for p in installed.iterdir():
        if p.name=='__pycache__' or p.name=='pip' or p.name.startswith('pip-') or p.suffix in {'.pyc','.pth'}:continue
        if p.is_dir():copy_tree(p,target/p.name)
        elif p.is_file() and p.name!='direct_url.json':shutil.copy2(p,target/p.name)
    # Screenshot processing uses imdecode, not video files. Omit this optional
    # FFmpeg plugin rather than redistributing an unrelated codec binary.
    (target/'cv2/opencv_videoio_ffmpeg500_64.dll').unlink(missing_ok=True)
    tools=portable/'tools';tools.mkdir();safe_extract(cache/'scrcpy-win64-v5.0.1.zip',tools)
    sources=portable/'third_party/sources';sources.mkdir(parents=True)
    for name,_,_ in ARTIFACTS[2:]:shutil.copy2(cache/name,sources/name)
    build_scripts=portable/'third_party/scrcpy-build';build_scripts.mkdir()
    for name in ('_init','ffmpeg.sh','libusb.sh','dav1d.sh','sdl.sh','adb_windows.sh'):
        p=cache/('scrcpy-'+name)
        if not p.exists():
            with urllib.request.urlopen('https://raw.githubusercontent.com/Genymobile/scrcpy/v5.0.1/app/deps/'+name,timeout=30) as r:p.write_bytes(r.read())
        shutil.copy2(p,build_scripts/name)
    manifest={'release':VERSION,'built_at_utc':datetime.now(timezone.utc).isoformat(),'platform':'Windows x64','python':'3.13.13','artifacts':artifacts,'excluded':['API keys','device authorization','runs','screenshots','chat data','developer venv'],'files':{p.relative_to(portable).as_posix():digest(p) for p in sorted(portable.rglob('*')) if p.is_file()}}
    (portable/'BUNDLE_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'source':str(source),'portable':str(portable),'source_files':audit(source),'portable_files':audit(portable),'status':'staged_needs_validation'},ensure_ascii=False),flush=True)
    (output/'build_paths.json').write_text(json.dumps({'source':str(source),'portable':str(portable)},indent=2),encoding='utf-8')

if __name__=='__main__':main()
