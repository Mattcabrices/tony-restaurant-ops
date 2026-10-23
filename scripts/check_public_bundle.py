"""Conservative public-tree guard; manual review remains necessary."""
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PATTERNS=[
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{25,}'),
    re.compile(r'\b[0-9]{7,12}:[A-Za-z0-9_-]{25,}'),
    re.compile(r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}'),
    re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+[^\s\"\']+'),
    re.compile(r'https://(?!example\.)(?!api\.)(?!developer\.)[a-z0-9-]+\.monday\.com/boards/\d{6,}',re.I),
]
FORBIDDEN_SUFFIXES={'.dpapi','.sqlite3','.sqlite','.db','.log','.xlsx','.docx','.pdf','.pem','.key','.zip'}


def check(files):
    problems=[]
    for name,data in files:
        path=Path(name)
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or (path.name.startswith('.env') and path.name!='.env.example'):
            problems.append(name+': private/generated file type')
        text=data.decode('utf-8',errors='replace')
        if any(p.search(text) for p in PATTERNS):problems.append(name+': possible credential/private identifier')
    return problems


def files():
    # Tracked files cannot hide behind .gitignore. Before Git initialization,
    # inspect source files while excluding local tool caches explicitly.
    if (ROOT/'.git').exists():
        command=subprocess.run(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT,capture_output=True,check=True)
        names=[n for n in command.stdout.decode().split('\0') if n]
        return [(n,(ROOT/n).read_bytes()) for n in names if (ROOT/n).is_file()]
    return [(p.relative_to(ROOT).as_posix(),p.read_bytes()) for p in ROOT.rglob('*') if p.is_file()
            and not set(p.relative_to(ROOT).parts)&{'.git','.venv','__pycache__','.pytest_cache'}]


if __name__=='__main__':
    problems=check(files())
    if problems:raise SystemExit('\n'.join(problems))
    print('Public-tree guard passed. Review staged content and full history before publication.')
