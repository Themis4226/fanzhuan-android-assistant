"""Compile a windowed launcher with the Windows .NET Framework compiler."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def build(output=None):
    output=Path(output or ROOT/'翻转外语助手.exe').resolve()
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():raise RuntimeError('开发机缺少 .NET Framework C# 编译器；最终用户可直接使用已编译的完整包。')
    result=subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/optimize+',
        '/codepage:65001','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll',
        '/reference:System.Web.Extensions.dll','/reference:System.Management.dll',
        '/out:'+str(output),str(ROOT/'desktop/Launcher.cs')],capture_output=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError(result.stdout.decode('utf-8',errors='replace')+result.stderr.decode('utf-8',errors='replace'))
    print('Built:',output,'SHA256:',hashlib.sha256(output.read_bytes()).hexdigest())
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path);a=p.parse_args();build(a.output)
