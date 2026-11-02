"""AppContainer entry point; standard library only, fixed numerical analysis."""
import importlib.util
import json
import math
import socket
import subprocess
import sys
from pathlib import Path


def probe(payload):
    denied = {}
    network_errors = {}
    for name, path in payload['deny_reads'].items():
        try:
            with open(path, 'rb') as stream:
                stream.read(1)
            denied[name] = False
        except PermissionError:
            denied[name] = True
    try:
        Path(payload['deny_write']).write_text('probe', encoding='utf-8')
        denied['outside_write'] = False
    except PermissionError:
        denied['outside_write'] = True
    for label, address in [('internet', ('1.1.1.1', 443)), ('loopback', ('127.0.0.1', payload['loopback_port']))]:
        with socket.socket() as connection:
            connection.settimeout(2)
            try:
                connection.connect(address)
                denied[label] = False
            except OSError as error:
                denied[label] = error.errno in (13, 1, 10013) or getattr(error, 'winerror', None) == 10013
                # Windows can silently drop AppContainer loopback connections.
                # The broker keeps this exact probe listener live throughout.
                if label == 'loopback' and isinstance(error, TimeoutError):
                    denied[label] = True
                network_errors[label] = {'errno': error.errno, 'winerror': getattr(error, 'winerror', None)}
    try:
        child = subprocess.run([sys.executable, '-I', '-S', '-c', 'pass'], timeout=3, capture_output=True)
        denied['child_process'] = False
    except OSError:
        denied['child_process'] = True
    return {'denials': denied, 'network_errors': network_errors, 'passed': all(denied.values())}


def main():
    source, destination = map(Path, sys.argv[1:3])
    if source.stat().st_size > 2_000_000:
        raise ValueError('Research input exceeds two megabytes.')
    payload = json.loads(source.read_text(encoding='utf-8'))
    if payload.get('mode') == 'probe':
        result = probe(payload)
    else:
        is_experiment = payload.get('mode') == 'experiment'
        expected = {'mode','template','rows','as_of_day'} if is_experiment else {'rows','target','as_of_day'}
        if is_experiment and payload.get('template') in {'bills_context','uga_context','school_context','rain_context'}:
            expected.add('features')
            if not isinstance(payload.get('features'),list) or len(payload['features'])>8000:
                raise ValueError('External-feature input exceeds fixed limits.')
        if set(payload) != expected or len(payload['rows']) > 4000:
            raise ValueError('Invalid fixed analysis request.')
        for row in payload['rows']:
            if set(row) - {'day', 'value', 'quality'} or not math.isfinite(row['value']):
                raise ValueError('Unexpected analysis input.')
        spec = importlib.util.spec_from_file_location('corner_engine', Path(__file__).with_name('engine.py'))
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        result = engine.experiment(payload) if is_experiment else engine.analyze(payload)
    destination.write_text(json.dumps(result, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
