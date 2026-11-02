"""Native Windows AppContainer with no capabilities and a one-process Job Object.

Profile creation and ACL changes happen only in the explicit setup command.
Research execution never falls back to an unrestricted subprocess.
"""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

PROFILE = 'TonyRestaurant.TonysCorner.Research.v1'


class IsolationUnavailable(RuntimeError):
    pass


def _windows():
    if os.name != 'nt':
        raise IsolationUnavailable('Windows AppContainer is required; unrestricted execution is disabled.')


def profile_sid(create=False, profile=PROFILE):
    _windows()
    userenv = ctypes.WinDLL('userenv', use_last_error=True)
    sid = ctypes.c_void_p()
    if create:
        function = userenv.CreateAppContainerProfile
        function.argtypes = [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, W.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        function.restype = ctypes.c_long
        result = function(profile, "Tony's isolated worker", 'Offline bounded restaurant research', None, 0, ctypes.byref(sid))
        if result not in (0, -2147024713):
            raise IsolationUnavailable('AppContainer profile creation failed: ' + str(result & 0xffffffff))
        if result != 0:
            sid = ctypes.c_void_p()
    if not sid.value:
        function = userenv.DeriveAppContainerSidFromAppContainerName
        function.argtypes = [W.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        function.restype = ctypes.c_long
        if function(profile, ctypes.byref(sid)) != 0:
            raise IsolationUnavailable('AppContainer SID could not be derived.')
    return sid


def sid_text(sid):
    import win32security
    return win32security.ConvertSidToStringSid(win32security.SID(ctypes.string_at(sid, ctypes.WinDLL('advapi32').GetLengthSid(sid))))


def grant(path, sid, writable=False, inherit=True):
    """Grant this AppContainer SID only; never relax production data ACLs."""
    import win32security
    import ntsecuritycon
    identity = win32security.ConvertStringSidToSid(sid)
    descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    rights = ntsecuritycon.FILE_GENERIC_READ | ntsecuritycon.FILE_GENERIC_EXECUTE
    if writable:
        rights |= ntsecuritycon.FILE_GENERIC_WRITE | ntsecuritycon.DELETE
    flags = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE if inherit else 0
    for index in range(acl.GetAceCount()):
        header, mask, existing_sid = acl.GetAce(index)
        if (header[0] == win32security.ACCESS_ALLOWED_ACE_TYPE and header[1] == flags
                and mask & rights == rights and win32security.ConvertSidToStringSid(existing_sid) == sid):
            return
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, flags, rights, identity)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION, None, None, acl, None)


class IsolatedWorker:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / 'isolated'
        self.config = self.root / 'isolation.json'

    def setup(self):
        _windows()
        from ..storage import protect_directory
        protect_directory(self.workspace)
        self.root.mkdir(exist_ok=True)
        runtime = self.root / 'runtime'
        runtime.mkdir(exist_ok=True)
        sid = profile_sid(create=True)
        try:
            identity = sid_text(sid)
        finally:
            ctypes.WinDLL('advapi32').FreeSid(sid)
        grant(self.workspace, identity, inherit=False)
        grant(self.root, identity, inherit=False)
        for name in ('worker.py', 'engine.py'):
            shutil.copyfile(Path(__file__).with_name(name), runtime / name)
        grant(runtime, identity)
        # Runtime under Program Files commonly has package read access already.
        # No ACL changes are made to Python, user profiles, source, or production data.
        self.config.write_text(json.dumps({'profile': PROFILE, 'sid': identity,
            'python': str(Path(sys.base_prefix) / 'python.exe'), 'verified': False}), encoding='utf-8')
        result = self.probe()
        config = json.loads(self.config.read_text(encoding='utf-8'))
        config.update(verified=result['passed'], checked_at=time.time(), probe=result)
        self.config.write_text(json.dumps(config), encoding='utf-8')
        if not result['passed']:
            raise IsolationUnavailable('Isolation probe failed; autonomous research remains disabled.')
        return result

    def status(self):
        if not self.config.exists():
            return {'ready': False, 'reason': 'AppContainer setup and denial tests have not been approved/run.'}
        value = json.loads(self.config.read_text(encoding='utf-8'))
        return {'ready': bool(value.get('verified')), 'checked_at': value.get('checked_at'), 'probe': value.get('probe')}

    def probe(self, deadline=None):
        decoy = self.workspace / 'probe-private.txt'
        decoy.write_text('synthetic isolation probe; no business data', encoding='utf-8')
        production = self.workspace.parent / 'data' / 'secrets.dpapi'
        reads = {'private_decoy': str(decoy)}
        if production.exists():
            reads['production_vault'] = str(production)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(4)
            port = listener.getsockname()[1]
            # A host control confirms reachability; no production service is used.
            with socket.create_connection(('127.0.0.1', port), timeout=2):
                pass
            return self._run({'mode': 'probe', 'deny_reads': reads, 'loopback_port': port,
                              'deny_write': str(self.workspace / 'probe-escape.txt')}, deadline)

    def analyze(self, payload, deadline=None):
        if not self.status()['ready']:
            raise IsolationUnavailable('Research execution is locked until AppContainer setup passes.')
        for name in ('worker.py', 'engine.py'):
            if Path(__file__).with_name(name).read_bytes() != (self.root / 'runtime' / name).read_bytes():
                raise IsolationUnavailable('Worker code changed; repeat approved isolation setup before execution.')
        probe = self.probe(deadline)
        if not probe.get('passed'):
            raise IsolationUnavailable('Isolation denial tests no longer pass; research stopped.')
        return self._run(payload, deadline)

    def _run(self, payload, deadline=None):
        _windows()
        if not self.config.exists():
            raise IsolationUnavailable('Isolation setup is missing.')
        config = json.loads(self.config.read_text(encoding='utf-8'))
        job = self.root / ('job-' + secrets.token_hex(8))
        output = job / 'output'
        output.mkdir(parents=True)
        source = job / 'input.json'
        source.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
        grant(job, config['sid'], inherit=False)
        grant(source, config['sid'], inherit=False)
        grant(output, config['sid'], writable=True)
        target = output / 'result.json'
        try:
            timeout_ms = min(30000, max(0, int((deadline-time.monotonic())*1000))) if deadline else 30000
            if timeout_ms < 500:
                raise IsolationUnavailable('Research wall-time budget exhausted before launch.')
            launch(config['python'], self.root / 'runtime' / 'worker.py', source, target, output, timeout_ms)
            if not target.exists() or target.stat().st_size > 2_000_000:
                raise IsolationUnavailable('Isolated worker produced no bounded result.')
            return json.loads(target.read_text(encoding='utf-8'))
        finally:
            # Only a freshly generated child of our verified isolation root is removed.
            if job.parent.resolve() == self.root and job.name.startswith('job-') and not job.is_symlink():
                shutil.rmtree(job)


def launch(python, script, source, target, working, timeout_ms=30000, profile=PROFILE):
    import win32job
    import win32api
    _windows()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class STARTUPINFO(ctypes.Structure):
        _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR), ('lpTitle', W.LPWSTR),
            ('dwX', W.DWORD), ('dwY', W.DWORD), ('dwXSize', W.DWORD), ('dwYSize', W.DWORD),
            ('dwXCountChars', W.DWORD), ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
            ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
            ('lpReserved2', ctypes.c_void_p), ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]
    class STARTUPINFOEX(ctypes.Structure):
        _fields_ = [('StartupInfo', STARTUPINFO), ('lpAttributeList', ctypes.c_void_p)]
    class PROCESSINFO(ctypes.Structure):
        _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE), ('pid', W.DWORD), ('tid', W.DWORD)]
    class CAPABILITIES(ctypes.Structure):
        _fields_ = [('sid', ctypes.c_void_p), ('capabilities', ctypes.c_void_p), ('count', W.DWORD), ('reserved', W.DWORD)]
    kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, W.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD, ctypes.c_void_p, W.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESSINFO)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.ResumeThread.argtypes = [W.HANDLE]
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
    kernel.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
    kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    size = ctypes.c_size_t()
    kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    if not kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    # Ensure the approved profile is registered in the launching Windows
    # session too (Task Scheduler can differ from the setup process).
    sid = profile_sid(create=True, profile=profile)
    capability = CAPABILITIES(sid, None, 0, 0)
    process = PROCESSINFO()
    job = win32job.CreateJobObject(None, '')
    try:
        if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x20009, ctypes.byref(capability), ctypes.sizeof(capability), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] = (win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
            win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY)
        limits['BasicLimitInformation']['ActiveProcessLimit'] = 1
        limits['ProcessMemoryLimit'] = 256 * 1024 * 1024
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(python), '-I', '-S', str(script), str(source), str(target)]))
        # Windows needs known profile locations to construct AppContainer redirects.
        # Inherit only these nonsecret path values, never the caller's full environment.
        safe_environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'SystemDrive', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA') if key in os.environ}
        safe_environment.update(TEMP=str(working), TMP=str(working))
        environment = ctypes.create_unicode_buffer('\0'.join(key+'='+value for key, value in sorted(safe_environment.items(), key=lambda item: item[0].upper())) + '\0\0')
        flags = 0x00080000 | 0x00000400 | 0x08000000 | 0x00000004
        if not kernel.CreateProcessW(str(python), command, None, None, False, flags, environment,
                                     str(working), ctypes.byref(startup), ctypes.byref(process)):
            raise IsolationUnavailable('AppContainer launch failed: ' + str(ctypes.get_last_error()))
        child_handle = win32api.OpenProcess(0x1F0FFF, False, process.pid)
        try:
            win32job.AssignProcessToJobObject(job, child_handle)
        finally:
            win32api.CloseHandle(child_handle)
        kernel.ResumeThread(process.thread)
        if kernel.WaitForSingleObject(process.process, timeout_ms) != 0:
            kernel.TerminateProcess(process.process, 1)
            raise IsolationUnavailable('Isolated process exceeded its wall-time limit.')
        exit_code = W.DWORD()
        kernel.GetExitCodeProcess(process.process, ctypes.byref(exit_code))
        if exit_code.value:
            raise IsolationUnavailable('Isolated worker failed with exit code ' + str(exit_code.value))
    finally:
        if process.thread:
            kernel.CloseHandle(process.thread)
        if process.process:
            kernel.TerminateProcess(process.process, 1)
            kernel.CloseHandle(process.process)
        win32api.CloseHandle(job)
        kernel.DeleteProcThreadAttributeList(attributes)
        ctypes.WinDLL('advapi32').FreeSid(sid)
