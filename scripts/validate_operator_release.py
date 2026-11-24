"""Review/validate the operator release without activating it. Default: dry run."""
import argparse
import hashlib
import json
from pathlib import Path

from app.code_sandbox import snapshot, fingerprint, test_patch
from app.storage import ROOT


def candidate():
    files=snapshot(ROOT); edits=[]; contents={}
    for name in ('index.html','app.js'):
        staged=ROOT/'tmp'/'operator-release-ui'/name
        if staged.exists():
            if staged.is_symlink() or staged.stat().st_size>2000000: raise RuntimeError('Invalid staged UI file.')
            relative='app/static/'+name;value=staged.read_bytes()
            files[relative]=value;contents[relative]=value;edits.append({'path':relative})
    return files,edits,contents


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate',action='store_true')
    args=parser.parse_args()
    files,edits,contents=candidate()
    manifest={name:hashlib.sha256(value).hexdigest() for name,value in files.items()}
    result={'dry_run':not args.validate,'production_activated':False,'schema':'operator v1, additive metadata only',
            'source_sha256':fingerprint(files),'base_sha256':fingerprint(snapshot(ROOT)),
            'staged_ui_files':[e['path'] for e in edits],
            'required_activation':['Protected database backup','Explicit approval for additive migration and graceful restart',
                                   'Promote only native-tested UI bytes','Authenticated read-only smoke checks'],
            'rollback':'TONY_OPERATOR_DISABLED=1 and graceful restart; retain all existing state.'}
    if args.validate:
        result['native']=test_patch(ROOT,edits,contents)
        if result['native']['source_sha256']!=result['source_sha256']: raise RuntimeError('Candidate changed during validation.')
        directory=ROOT/'tmp'/'operator-release-evidence';directory.mkdir(parents=True,exist_ok=True)
        (directory/'files.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        (directory/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
