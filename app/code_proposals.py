"""Explicit, hash-bound Telegram review for locally generated source patches."""
import asyncio
import ast
import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .storage import ROOT
from .code_sandbox import test_patch


PROTECTED = {'app/code_proposals.py', 'app/telegram.py', 'app/storage.py',
             'app/main.py', 'app/manage.py', 'app/approved_tasks.py', 'app/public_reader.py',
             'app/code_sandbox.py', 'app/code_test_runner.py', 'app/request_limits.py',
             'app/context_policy.py', 'app/exceptions.py', 'app/source_health.py', 'app/verified_api.py',
             'app/guest_lookup.py', 'app/writeups.py', 'scripts/render_writeup.py', 'app/dialogue.py', 'app/intent_router.py',
             'app/conversation_plan.py','app/conversation_archive.py','app/guest_reply.py',
             'app/booked_orders.py','app/whole_pizzas.py','app/providers.py','app/restaurant_profile.py',
             'app/booking_index.py','app/business_lookup.py','app/sales_breakdown.py'}
MAX_FILES = 8
MAX_DIFF = 14000


def allowed_path(relative):
    if not isinstance(relative, str):
        return False
    normalized = relative.replace('\\', '/')
    path = Path(relative)
    if (not re.fullmatch(r'[A-Za-z0-9_./-]+', normalized) or path.drive or path.is_absolute()
            or any(part in {'','.','..'} or part.endswith(('.', ' '))
                   or part.split('.')[0].casefold() in {'con','prn','aux','nul',
                       *(f'com{i}' for i in range(1,10)), *(f'lpt{i}' for i in range(1,10))}
                   for part in normalized.split('/'))
            or normalized.casefold() in {name.casefold() for name in PROTECTED}
            or normalized.casefold().startswith('app/corner/')):
        return False
    parts = path.parts
    if relative == 'README.md':
        return True
    if len(parts) >= 2 and parts[0] == 'app':
        return path.suffix == '.py' or (len(parts) >= 3 and parts[1] == 'static' and path.suffix in {'.js', '.css', '.html'})
    return len(parts) >= 2 and parts[0] == 'tests' and path.suffix == '.py'


def source_files(root):
    result = {}
    for directory in ('app', 'tests'):
        for path in (root / directory).rglob('*'):
            if path.is_file() and not path.is_symlink() and not any(p.is_junction() or p.is_symlink() for p in path.parents if p != root):
                relative = path.relative_to(root).as_posix()
                if allowed_path(relative):
                    result[relative] = path.read_bytes()
    result['README.md'] = (root / 'README.md').read_bytes()
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def codex_executable():
    direct = shutil.which('codex')
    if direct:
        return direct
    local = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI' / 'Codex' / 'bin'
    candidates = sorted(local.glob('*/codex.exe'), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0]) if candidates else None


class CodeProposals:
    def __init__(self, db, root=ROOT, executable=None, ai=None):
        self.db = db
        self.root = Path(root)
        self.ai = ai
        self.executable = executable or codex_executable()
        self.directory = self.root / 'data' / 'code-proposals'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.approval_lock = asyncio.Lock()

    def path(self, proposal_id):
        if len(proposal_id) != 12 or any(c not in '0123456789abcdef' for c in proposal_id):
            raise ValueError('Invalid proposal ID.')
        return self.directory / proposal_id

    def read(self, proposal_id):
        path = self.path(proposal_id) / 'manifest.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def recover_incomplete(self):
        """Undo an interrupted multi-file apply before accepting new commands."""
        for folder in self.directory.iterdir():
            if not folder.is_dir():
                continue
            try:
                manifest = self.read(folder.name)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            if not manifest or manifest.get('status') != 'applying':
                continue
            for edit in manifest['edits']:
                relative = edit['path']
                if not allowed_path(relative):
                    raise RuntimeError('Interrupted proposal has a protected path; manual recovery required.')
                target = self.root / relative
                old = folder / 'base' / relative
                if old.exists():
                    content = old.read_bytes()
                    if digest(content) != edit['base_sha']:
                        raise RuntimeError('Interrupted proposal backup failed verification.')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                elif edit['base_sha'] is None:
                    target.unlink(missing_ok=True)
                else:
                    raise RuntimeError('Interrupted proposal backup is missing.')
            manifest['status'] = 'recovered_rolled_back'
            self.save(folder.name, manifest)

    def save(self, proposal_id, manifest):
        path = self.path(proposal_id)
        path.mkdir(exist_ok=True)
        temp = path / 'manifest.tmp'
        temp.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, path / 'manifest.json')

    @staticmethod
    def response_json(response):
        answer = '\n'.join(c.get('text', '') for item in response.get('output', [])
                           if item.get('type') == 'message' for c in item.get('content', [])
                           if c.get('type') == 'output_text').strip()
        if answer.startswith('```'):
            answer = answer.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        value = json.loads(answer)
        if not isinstance(value, dict):
            raise ValueError('Code proposal response was not a JSON object.')
        return value

    async def generate_with_ai(self, stage, base, request):
        if not self.ai.status().get('connected'):
            raise RuntimeError('Tony must be signed in to ChatGPT before preparing code proposals.')
        files = [{'path': name, 'bytes': len(data)} for name, data in base.items()]
        selection = self.response_json(await self.ai.response([
            {'role': 'developer', 'content': 'Select up to three source files needed for a requested code change. Return only JSON: {"paths":["app/example.py"]}. You may name a new allowed app or test file. Never select app/code_proposals.py, app/telegram.py, app/storage.py, app/main.py, app/manage.py, data, secrets, or dependencies.'},
            {'role': 'user', 'content': 'Request: ' + request + '\nAvailable files: ' + json.dumps(files)},
        ], []))
        paths = selection.get('paths')
        if (not isinstance(paths, list) or not 1 <= len(paths) <= 3
                or any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths)):
            raise ValueError('Code proposal could not choose a valid set of files.')
        if any(not isinstance(path, str) or not allowed_path(path) for path in paths):
            raise ValueError('Code proposal selected a protected or invalid path.')
        selected = {path: base.get(path, b'').decode('utf-8') for path in paths}
        if sum(len(value) for value in selected.values()) > 70000:
            raise ValueError('Selected source is too large; make a narrower code request.')
        guidance = ('Prepare only exact text replacements for the requested change. Return JSON only in this format: '
                    '{"edits":[{"path":"app/example.py","search":"exact existing text",'
                    '"replace":"replacement text"}]}. For a new file use search="" and replace with '
                    'its complete content. Each search must match exactly once. Do not execute code, '
                    'request tools, access data or credentials, change protected files, or add dependencies. '
                    'Do not claim the edits were applied. Keep the proposal small and preserve unrelated behavior.')
        result = self.response_json(await self.ai.response([
            {'role': 'developer', 'content': guidance},
            {'role': 'user', 'content': 'Request: ' + request + '\nSelected source files (JSON):\n' + json.dumps(selected)},
        ], []))
        edits = result.get('edits')
        if not isinstance(edits, list) or not 1 <= len(edits) <= 12:
            raise ValueError('Code proposal returned no bounded text edits.')
        for edit in edits:
            if not isinstance(edit, dict) or edit.get('path') not in selected:
                raise ValueError('Code proposal edited an unselected file.')
            search, replacement = edit.get('search'), edit.get('replace')
            if not isinstance(search, str) or not isinstance(replacement, str) or len(replacement) > 15000:
                raise ValueError('Code proposal contains an invalid or oversized replacement.')
            path = edit['path']
            target = stage / path
            current = target.read_bytes().decode('utf-8') if target.exists() else ''
            if not search:
                if target.exists():
                    raise ValueError('Empty search is only allowed when creating a new file.')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(replacement.encode('utf-8'))
            else:
                if current.count(search) != 1:
                    raise ValueError('A proposed search fragment did not match exactly once; no live code changed.')
                target.write_bytes(current.replace(search, replacement, 1).encode('utf-8'))

    async def propose(self, request):
        if not self.ai:
            raise RuntimeError("Code proposals require Tony's no-tools model route. Unrestricted CLI generation is disabled.")
        if not request or len(request) > 1200:
            raise ValueError('Use /code followed by a request of 1–1200 characters.')
        proposal_id = secrets.token_hex(6)
        self.save(proposal_id, {'id': proposal_id, 'status': 'generating', 'request': request,
                                'created': time.time()})
        base = source_files(self.root)
        try:
            with tempfile.TemporaryDirectory(prefix='tony-code-') as folder:
                stage = Path(folder)
                for relative, content in base.items():
                    destination = stage / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                (stage / 'requirements.txt').write_bytes((self.root / 'requirements.txt').read_bytes())
                await self.generate_with_ai(stage, base, request)
                revised = source_files(stage)
                changed = sorted(k for k in base.keys() | revised.keys() if base.get(k) != revised.get(k))
                if not changed:
                    raise RuntimeError('The proposal produced no source changes for this request.')
                if len(changed) > MAX_FILES:
                    raise RuntimeError('Proposal changes too many files; split the request into smaller pieces.')
                edits, patch = [], []
                for relative in changed:
                    if not allowed_path(relative):
                        raise RuntimeError('Proposal changed a protected path and was rejected.')
                    old, new = base.get(relative), revised.get(relative)
                    if new is not None and len(new) > 100000:
                        raise RuntimeError('Proposal contains an oversized source file.')
                    try:
                        old_text = old.decode('utf-8') if old is not None else ''
                        new_text = new.decode('utf-8') if new is not None else ''
                    except UnicodeDecodeError:
                        raise RuntimeError('Proposal contains a non-text file.') from None
                    if relative.endswith('.py') and new is not None:
                        ast.parse(new_text, filename=relative)
                    patch.extend(difflib.unified_diff(old_text.splitlines(keepends=True),
                                                       new_text.splitlines(keepends=True),
                                                       fromfile='a/' + relative, tofile='b/' + relative))
                    edits.append({'path': relative, 'base_sha': digest(old) if old is not None else None,
                                  'new_sha': digest(new) if new is not None else None})
                    if new is not None:
                        destination = self.path(proposal_id) / 'new' / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(new)
                diff = ''.join(patch)
                if not diff or len(diff) > MAX_DIFF:
                    raise RuntimeError('Proposal diff is too large for Telegram review; split the request.')
                sha = digest(diff.encode('utf-8'))
                (self.path(proposal_id) / 'diff.txt').write_bytes(diff.encode('utf-8'))
                manifest = {'id': proposal_id, 'status': 'prepared', 'request': request,
                            'created': time.time(), 'expires': time.time() + 24 * 3600,
                            'sha256': sha, 'edits': edits}
                self.save(proposal_id, manifest)
                return manifest, diff
        except Exception:
            current = self.read(proposal_id) or {'id': proposal_id}
            current['status'] = 'failed'
            self.save(proposal_id, current)
            raise

    def mark_reviewable(self, proposal_id):
        manifest = self.read(proposal_id)
        if manifest and manifest['status'] == 'prepared':
            manifest['status'] = 'reviewable'
            self.save(proposal_id, manifest)

    def reject(self, proposal_id):
        manifest = self.read(proposal_id)
        if not manifest or manifest['status'] != 'reviewable':
            raise ValueError('No reviewable proposal with that ID.')
        manifest['status'] = 'rejected'
        self.save(proposal_id, manifest)
        return manifest

    async def approve(self, proposal_id, sha_prefix):
        async with self.approval_lock:
            return await self._approve(proposal_id, sha_prefix)

    async def _approve(self, proposal_id, sha_prefix):
        manifest = self.read(proposal_id)
        if not manifest or manifest['status'] not in {'reviewable', 'applied'}:
            raise ValueError('No reviewable proposal with that ID.')
        if len(sha_prefix) != 16 or sha_prefix != manifest['sha256'][:16]:
            raise ValueError('Diff hash does not match. Review the patch and use its exact approval command.')
        if manifest['status'] == 'applied':
            return manifest
        if time.time() > manifest['expires']:
            raise ValueError('Proposal expired; request a fresh patch.')
        patch = (self.path(proposal_id) / 'diff.txt').read_bytes()
        if digest(patch) != manifest['sha256']:
            raise RuntimeError('Stored diff changed; approval refused.')
        for edit in manifest['edits']:
            relative = edit['path']
            if not allowed_path(relative):
                raise RuntimeError('Proposal includes a protected path.')
            target = self.root / relative
            if target.is_symlink() or any(p.is_symlink() or p.is_junction() for p in target.parents if p != self.root):
                raise RuntimeError('Symlink in proposed path; approval refused.')
            current = target.read_bytes() if target.exists() else None
            if (digest(current) if current is not None else None) != edit['base_sha']:
                raise RuntimeError(f'{relative} changed since review; request a fresh patch.')
            new = self.path(proposal_id) / 'new' / relative
            if (digest(new.read_bytes()) if new.exists() else None) != edit['new_sha']:
                raise RuntimeError('Stored proposal content changed; approval refused.')
        contents = {e['path']: ((self.path(proposal_id)/'new'/e['path']).read_bytes()
                    if (self.path(proposal_id)/'new'/e['path']).exists() else None) for e in manifest['edits']}
        # Bind the displayed diff to exactly the bytes about to be tested/promoted.
        reconstructed = []
        for edit in manifest['edits']:
            relative = edit['path']
            target = self.root/relative
            old = target.read_bytes().decode('utf-8') if target.exists() else ''
            new = contents[relative].decode('utf-8') if contents[relative] is not None else ''
            reconstructed.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile='a/'+relative, tofile='b/'+relative))
        if ''.join(reconstructed).encode('utf-8') != patch:
            raise RuntimeError('Stored diff does not match proposed bytes; approval refused.')
        manifest['validation'] = await asyncio.to_thread(test_patch, self.root, manifest['edits'], contents)
        for edit in manifest['edits']:
            target = self.root/edit['path']
            current = target.read_bytes() if target.exists() else None
            if (digest(current) if current is not None else None) != edit['base_sha']:
                raise RuntimeError('Live source changed after isolated validation; request a fresh patch.')
        originals = {e['path']: (self.root / e['path']).read_bytes() if (self.root / e['path']).exists() else None
                     for e in manifest['edits']}
        for relative, old in originals.items():
            if old is not None:
                backup = self.path(proposal_id) / 'base' / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(old)
        manifest['status'] = 'applying'
        self.save(proposal_id, manifest)
        try:
            for edit in manifest['edits']:
                target = self.root / edit['path']
                new = self.path(proposal_id) / 'new' / edit['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                if contents[edit['path']] is not None:
                    temp = target.with_name(target.name + '.tony-new')
                    temp.write_bytes(contents[edit['path']])
                    os.replace(temp, target)
                else:
                    target.unlink(missing_ok=True)
            manifest['status'] = 'applied'
            self.save(proposal_id, manifest)
            return manifest
        except Exception:
            for relative, old in originals.items():
                target = self.root / relative
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(old)
            manifest['status'] = 'failed_rolled_back'
            self.save(proposal_id, manifest)
            raise

    def restart_after_reply(self):
        # The helper must outlive the server process it asks the manager to restart.
        helper = self.root / 'scripts' / 'restart_after_code.py'
        python = self.root / '.venv' / 'Scripts' / 'python.exe'
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.Popen([str(python), str(helper)], cwd=self.root,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags)
