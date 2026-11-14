"""Destructive dependency smoke test, only for an explicitly isolated Compose project.

Run after `up --wait`; never point this at an existing development/production stack.
COMPOSE_FILE and other Compose environment overrides are honored.
"""
from http.client import HTTPException
import argparse
import json
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument('--env-file')
parser.add_argument('--project', required=True)
parser.add_argument('--port', required=True, type=int)
args = parser.parse_args()
if not args.project.startswith('kc-hw05-'):
    parser.error('project must start with kc-hw05-; use fresh dedicated volumes')
if not 1024 <= args.port <= 65535:
    parser.error('choose an unprivileged local port')
compose = ['docker', 'compose', '-p', args.project]
if args.env_file:
    compose += ['--env-file', args.env_file]
base = f'http://127.0.0.1:{args.port}'
actual = subprocess.check_output([*compose, 'port', 'web', '8000'], text=True).strip()
if actual != f'127.0.0.1:{args.port}':
    parser.error('requested port does not belong to this Compose project')



def check(pg=True, kafka=True):
    deadline = time.monotonic() + 45
    while True:
        try:
            with urlopen(base + '/health/live', timeout=8) as response:
                assert response.status == 200
                assert json.load(response) == {'status': 'live'}
            try:
                response = urlopen(base + '/health/ready', timeout=8)
            except HTTPError as error:
                response = error
            with response:
                result = json.load(response)
                assert response.status == (200 if pg and kafka else 503), result
                assert result == {'status': 'ready' if pg and kafka else 'not_ready',
                                  'checks': {'postgres': pg, 'kafka': kafka}}, result
            print(f'PASS live=200 ready={200 if pg and kafka else 503} postgres={pg} kafka={kafka}', flush=True)
            return
        except (AssertionError, URLError, TimeoutError, HTTPException) as error:
            if time.monotonic() >= deadline:
                raise AssertionError(f'Health matrix did not converge: {error}') from error
            time.sleep(1)


check()
for service, expected in [('db', {'pg': False}), ('kafka', {'kafka': False})]:
    subprocess.run([*compose, 'stop', '--timeout', '10', service], check=True)
    try:
        check(**expected)
    finally:
        subprocess.run([*compose, 'start', service], check=True)
    check()
subprocess.run([*compose, 'down', '--timeout', '10'], check=True)
subprocess.run([*compose, 'up', '-d', '--wait', '--wait-timeout', '180'], check=True)
check()
print('PASS dependency failures, recoveries and full restart', flush=True)
