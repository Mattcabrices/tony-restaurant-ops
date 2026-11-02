"""Generated tests never execute under the unrestricted production token."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys

from .corner.isolation import IsolationUnavailable, grant, launch, profile_sid, sid_text
from .storage import protect_directory

PROFILE = 'TonyRestaurant.Tony.CodeTests.v1'
EXTENSIONS = {'.py', '.json', '.md', '.js', '.css', '.html'}


def snapshot(root):
    """Source only. Never traverse symlinks/junctions into state or profiles."""
    root = Path(root).resolve()
    files = {}
    for folder in ('app', 'tests', 'scripts', 'tony_demo'):
        for base, directories, names in os.walk(root/folder, followlinks=False):
            directories[:] = [n for n in directories if n != '__pycache__' and
                not Path(base, n).is_symlink() and not Path(base, n).is_junction()]
            for name in names:
                path = Path(base, name)
                if path.is_symlink() or path.suffix not in EXTENSIONS:
                    continue
                if path.stat().st_size > 2_000_000:
                    raise IsolationUnavailable('Source file exceeds isolated-test size limit.')
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    for name in ('requirements.txt', 'pytest.ini', 'README.md'):
        path = root/name
        if path.exists() and not path.is_symlink():
            files[name] = path.read_bytes()
    return files


def fingerprint(files):
    return hashlib.sha256(json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())},
                                     sort_keys=True).encode()).hexdigest()


def test_patch(root, edits, contents):
    """Tests an immutable snapshot plus exact proposed bytes; no live edits."""
    if os.name != 'nt':
        raise IsolationUnavailable('Code approval requires Windows AppContainer; no unrestricted fallback.')
    root = Path(root).resolve()
    before = snapshot(root)
    candidate = dict(before)
    for edit in edits:
        relative = edit['path']
        path = Path(relative)
        if (not isinstance(relative,str) or path.is_absolute() or path.drive or ':' in relative
                or '..' in relative.replace('\\','/').split('/')
                or relative.replace('\\','/') not in {path.as_posix()}):
            raise IsolationUnavailable('Invalid isolated candidate path.')
        value = contents[edit['path']]
        if value is None:
            candidate.pop(edit['path'], None)
        else:
            candidate[edit['path']] = value
    sandbox = root/'tmp'/('code-isolated-'+secrets.token_hex(10))
    sandbox.mkdir(parents=True)
    protect_directory(sandbox)
    try:
        sid = profile_sid(create=True, profile=PROFILE)
        try:
            identity = sid_text(sid)
        finally:
            ctypes.WinDLL('advapi32').FreeSid(sid)
        source, packages, output = sandbox/'source', sandbox/'packages', sandbox/'output'
        source.mkdir(); output.mkdir()
        for name, content in candidate.items():
            path = source/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        # Dependency code is copied, never installed and never fetched by tests.
        dependency_path = Path(sys.prefix)/'Lib'/'site-packages'
        if not dependency_path.is_dir():
            raise IsolationUnavailable('Validated Windows dependency runtime is unavailable.')
        shutil.copytree(dependency_path, packages, ignore=shutil.ignore_patterns('__pycache__', 'pip', 'pip-*.dist-info'))
        runner = sandbox/'runner.py'
        runner.write_bytes(Path(__file__).with_name('code_test_runner.py').read_bytes())
        probe_script = sandbox/'probe.py'
        probe_script.write_bytes(Path(__file__).with_name('corner').joinpath('worker.py').read_bytes())
        settings = sandbox/'settings.json'
        data = source/'data'; data.mkdir()
        settings.write_text(json.dumps({'source': str(source), 'packages': str(packages),
            'data': str(data), 'scratch': str(output/'scratch')}), encoding='utf-8')
        grant(sandbox, identity, inherit=False)
        for path in (source, packages, runner, probe_script, settings):
            grant(path, identity)
        grant(data, identity, writable=True)
        grant(output, identity, writable=True)
        python = Path(sys.base_prefix)/'python.exe'
        decoy = sandbox/'private-decoy.txt'
        decoy.write_text('synthetic secret; must be denied', encoding='utf-8')
        probe_input = sandbox/'probe-input.json'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(4)
            with socket.create_connection(listener.getsockname(), timeout=2):
                pass
            reads = {'private_decoy': str(decoy)}
            vault = root/'data'/'secrets.dpapi'
            if vault.exists():
                reads['production_vault'] = str(vault)
            probe_input.write_text(json.dumps({'mode': 'probe', 'deny_reads': reads,
                'deny_write': str(root/'data'/'code-probe-escape.txt'),
                'loopback_port': listener.getsockname()[1]}), encoding='utf-8')
            grant(probe_input, identity, inherit=False)
            probe_result = output/'probe.json'
            launch(python, probe_script, probe_input, probe_result, output, profile=PROFILE)
            probe = json.loads(probe_result.read_text(encoding='utf-8'))
        if not probe.get('passed'):
            raise IsolationUnavailable('Generated-code isolation denial probes failed; no live files changed.')
        target = output/'result.json'
        launch(python, runner, settings, target, output, timeout_ms=180000, profile=PROFILE)
        if not target.exists() or target.stat().st_size > 10000:
            raise IsolationUnavailable('Isolated tests produced no bounded result; no live files changed.')
        result = json.loads(target.read_text(encoding='utf-8'))
        # Never promote altered test/source bytes or a snapshot of stale production.
        if snapshot(source) != candidate or snapshot(root) != before:
            raise IsolationUnavailable('Source changed during isolated tests; request a fresh reviewed patch.')
        if result.get('returncode') != 0:
            raise IsolationUnavailable('Isolated tests failed; no live files changed. '+json.dumps(result)[-1600:])
        return {'isolation': 'Windows AppContainer; no capabilities; one process; 256 MB; 180 seconds',
                'probe': probe, 'source_sha256': fingerprint(candidate), 'tests_passed': True,
                'test_count': result.get('passed'), 'host_only_tests': result.get('host_only_tests',[])}
    finally:
        if sandbox.parent.resolve() == (root/'tmp').resolve() and not sandbox.is_symlink():
            shutil.rmtree(sandbox)
