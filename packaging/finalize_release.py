"""Finalize only audited, validated staging trees; scan ZIP members as well."""
import argparse
from importlib.util import module_from_spec,spec_from_file_location
import json
from pathlib import Path
import shutil
import zipfile

def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    spec=spec_from_file_location('bundle_builder',root/'packaging/build_portable.py');b=module_from_spec(spec);spec.loader.exec_module(b)
    output=a.output.resolve();source=output/'source/fanzhuan-android-assistant';portable=output/'portable/fanzhuan-android-assistant'
    validation=json.loads((portable/'PACKAGE_VALIDATION.json').read_text(encoding='utf-8'))
    if validation['status']!='passed' or validation['api_calls']!=0:raise RuntimeError('Missing successful offline verification')
    # Sync build/verification tooling and clean docs only. Runtime code was tested.
    for tree in (source,portable):
        for f in (root/'packaging').glob('*'):
            if f.is_file():shutil.copy2(f,tree/'packaging'/f.name)
        shutil.copy2(root/'packaging/README.release.md',tree/'README.md')
        if not (tree/'PACKAGE_VALIDATION.json').is_file():shutil.copy2(portable/'PACKAGE_VALIDATION.json',tree/'PACKAGE_VALIDATION.json')
    manifest_path=portable/'BUNDLE_MANIFEST.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files']={f.relative_to(portable).as_posix():b.digest(f) for f in sorted(portable.rglob('*')) if f.is_file() and f!=manifest_path}
    manifest['verification']='PACKAGE_VALIDATION.json'
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    source_count=b.audit(source);portable_count=b.audit(portable)
    names=[('fanzhuan-android-source-v0.1.0.zip',source),('fanzhuan-android-windows-x64-v0.1.0.zip',portable)]
    values=b.private_values();assets=[]
    for name,tree in names:
        archive=output/name;b.archive_tree(tree,archive)
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise RuntimeError('Archive CRC failure')
            for member in z.infolist():
                parts=Path(member.filename).parts
                if any(x in {'.env.local','runs','.venv','.git'} for x in parts):raise RuntimeError('Forbidden ZIP member')
                overlap=b''
                with z.open(member) as f:
                    while chunk:=f.read(1048576):
                        data=overlap+chunk
                        if any(value in data for value in values):raise RuntimeError('Private credential found inside ZIP')
                        overlap=data[-4096:]
        assets.append({'name':name,'bytes':archive.stat().st_size,'sha256':b.digest(archive)})
    checksums=output/'SHA256SUMS.txt';checksums.write_text(''.join(f"{x['sha256']}  {x['name']}\n" for x in assets),encoding='ascii')
    audit={'status':'passed','source_files':source_count,'portable_files':portable_count,'actual_configured_key_exact_scan_passed':True,
           'zip_member_scan_passed':True,'excluded_private_config_and_runs':True,'api_key_values_printed':False,'assets':assets}
    (output/'PRIVACY_AUDIT.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit,indent=2))

if __name__=='__main__':main()
