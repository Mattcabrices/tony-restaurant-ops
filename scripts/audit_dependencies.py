"""Scan only public pinned package identities via OSV; no installation or secrets.

Protocol: https://google.github.io/osv.dev/post-v1-querybatch/
Exit 0: completed/no findings; 1: findings; 2: unavailable/incomplete/malformed.
"""
import json
from pathlib import Path
import re
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]


def pinned_packages(path):
    pairs = re.findall(r'^([A-Za-z0-9_.-]+)==([^\s;\\]+)', path.read_text(encoding='utf-8'), re.M)
    if not pairs or len(pairs) > 200:
        raise ValueError('Expected a bounded dependency hash lock.')
    return pairs


def scan(path, http=None):
    packages = pinned_packages(Path(path))
    own = http is None
    client = http or httpx.Client(timeout=30, trust_env=False, follow_redirects=False)
    queries = [{'package':{'ecosystem':'PyPI','name':name},'version':version} for name,version in packages]
    try:
        findings = []
        pending = list(enumerate(queries))
        for page in range(5):
            response = client.post('https://api.osv.dev/v1/querybatch',json={'queries':[q for _,q in pending]})
            response.raise_for_status()
            results = response.json().get('results')
            if not isinstance(results,list) or len(results) != len(pending):
                raise ValueError('OSV response shape was incomplete.')
            following = []
            for (index, query), result in zip(pending,results):
                if not isinstance(result,dict):
                    raise ValueError('OSV response was malformed.')
                vulnerabilities = result.get('vulns',[])
                if not isinstance(vulnerabilities,list):
                    raise ValueError('OSV vulnerability list was malformed.')
                for vuln in vulnerabilities:
                    identity = vuln.get('id') if isinstance(vuln,dict) else None
                    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,150}',identity):
                        raise ValueError('OSV vulnerability identity was malformed.')
                    name,version = packages[index]
                    finding = {'package':name,'version':version,'id':identity,'url':'https://osv.dev/vulnerability/'+identity}
                    if finding not in findings:
                        findings.append(finding)
                if result.get('next_page_token'):
                    following.append((index,{**query,'page_token':result['next_page_token']}))
            pending = following
            if not pending:
                return {'source':'OSV public dependency vulnerability API','complete':True,
                        'packages_checked':len(packages),'findings':findings,
                        'limitations':['No reported match does not prove absence of vulnerabilities. No dependencies were changed.']}
        raise ValueError('OSV pagination limit reached; scan incomplete.')
    finally:
        if own:
            client.close()


if __name__ == '__main__':
    try:
        result = scan(ROOT/'requirements.lock')
        print(json.dumps(result,indent=2))
        sys.exit(1 if result['findings'] else 0)
    except (httpx.HTTPError,ValueError,KeyError,OSError):
        print(json.dumps({'complete':False,'error':'Dependency vulnerability scan unavailable; do not treat this as a clean result.'}))
        sys.exit(2)
