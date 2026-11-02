"""Trusted bootstrap copied into an offline disposable AppContainer."""
import json
import os
from pathlib import Path
import site
import sys


def main():
    settings, target = map(Path, sys.argv[1:3])
    config = json.loads(settings.read_text(encoding='utf-8'))
    root = Path(config['source'])
    packages = Path(config['packages'])
    handles = []
    for folder in (packages/'pywin32_system32', packages/'win32'):
        if folder.exists() and hasattr(os, 'add_dll_directory'):
            handles.append(os.add_dll_directory(str(folder)))
    site.addsitedir(str(packages))
    sys.path.insert(0, str(root))
    os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1',
                      TONY_DATA_DIR=config['data'])
    # Python 3.14 mkdir(mode=0700) replaces inherited Windows ACLs with the
    # owner/SYSTEM only. That drops this restricted token's package SID and
    # makes pytest's own temporary directory unreadable. Preserve the broker's
    # restrictive inherited ACL instead; this grants no new host access.
    native_mkdir = os.mkdir

    def inherited_mkdir(path, mode=0o777, *, dir_fd=None):
        return native_mkdir(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)

    os.mkdir = inherited_mkdir
    # Only the already-declared async plugin; no installed third-party auto-loading.
    import pytest
    class Outcomes:
        def __init__(self):
            self.failures = []
            self.passed = 0

        def pytest_runtest_logreport(self, report):
            if report.failed:
                self.failures.append(report.nodeid[:250])
            elif report.when == 'call' and report.passed:
                self.passed += 1

        def pytest_collectreport(self, report):
            if report.failed:
                self.failures.append(report.nodeid[:250])

    outcomes = Outcomes()
    with (target.parent/'pytest.log').open('w', encoding='utf-8') as log:
        sys.stdout = sys.stderr = log
        status = pytest.main(['-q', '-p', 'pytest_asyncio.plugin', '--tb=short',
            '--basetemp', config['scratch'], '-o', 'cache_dir='+str(target.parent/'cache'),
            # This positive host-locking test deliberately starts a second
            # process. The code jail forbids children; run it on the host as a
            # separate release gate, and verify child denial here instead.
            '--deselect=tests/test_server_lock.py::test_second_process_exits_cleanly_and_lock_releases',
            str(root/'tests')], plugins=[outcomes])
    target.write_text(json.dumps({'returncode': int(status), 'passed': outcomes.passed,
                                 'failed_tests': outcomes.failures[:15],
                                 'host_only_tests':['tests/test_server_lock.py::test_second_process_exits_cleanly_and_lock_releases']}), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        # This contains only disposable paths/dependencies; never production data.
        import traceback
        Path(sys.argv[2]).write_text(json.dumps({'returncode':2,
            'bootstrap_error':type(error).__name__, 'message':str(error)[:300], 'diagnostic':traceback.format_exc()[-900:]}), encoding='utf-8')
