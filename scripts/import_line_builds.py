"""Import owner-provided serving-build JSON. No bundled recipes or portions."""
import argparse
import hashlib
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from app.storage import ROOT,protect_directory


def validate(value):
    if not isinstance(value,dict) or set(value)!={'source_title','builds'} or not isinstance(value['source_title'],str) or len(value['source_title'])>200:
        raise ValueError('Use source_title and builds only.')
    if not isinstance(value['builds'],list) or not 1<=len(value['builds'])<=300:raise ValueError('Use 1-300 builds.')
    for row in value['builds']:
        if not isinstance(row,dict) or set(row)-{'name','aliases','source_section','components','sizes','notes'} or not isinstance(row.get('name'),str):
            raise ValueError('Invalid serving build schema.')
    if len(json.dumps(value))>500000:raise ValueError('Build catalog too large.')
    return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('file',type=Path);parser.add_argument('--confirm',action='store_true');args=parser.parse_args()
    if not args.confirm:raise SystemExit('Owner confirmation required: --confirm')
    if args.file.stat().st_size>500000:raise SystemExit('Build catalog too large.')
    raw=args.file.read_bytes();value=validate(json.loads(raw))
    value.update(source_sha256=hashlib.sha256(raw).hexdigest(),imported_at=datetime.now(timezone.utc).isoformat(),scope='Owner-entered standard serving builds; not measured consumption or costs.')
    folder=ROOT/'data';protect_directory(folder);pending=folder/'line_builds.pending'
    pending.write_text(json.dumps(value),encoding='utf-8');os.replace(pending,folder/'line_builds.json')
    print('Serving builds imported locally; no external requests.')


if __name__=='__main__':main()
