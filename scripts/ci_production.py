"""Isolated P01-P05 CI orchestration. Raw logs never reach the Actions console.

prepare/run/finalize are separate so finalize can run under Actions always().
Only this run's new project and private state are eligible for cleanup.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SECRET_NAMES = {'POSTGRES_PASSWORD', 'DEV_DB_PASSWORD', 'TEST_DB_PASSWORD', 'DJANGO_SECRET_KEY'}
KAFKA_TESTS = {
    'bookings.tests.test_kafka_pipeline.KafkaIntegrationTests.test_invalid_record_is_redelivered_and_blocks_later_message',
    'bookings.tests.test_kafka_pipeline.KafkaIntegrationTests.test_forecast_outbox_real_broker_consumer_and_redelivery',
}


def validate_django(data):
    names = data['discovered_ids']
    if data['tests_run'] < 221 or data['tests_run'] != len(names) or len(set(names)) != len(names):
        raise ValueError('Django executed count does not match discovery or is below 221')
    if not KAFKA_TESTS <= set(names):
        raise ValueError('required real Kafka integration tests are missing')
    for field in ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'):
        if data[field] != 0:
            raise ValueError('Django suite contains non-passing outcomes')


def validate_js(text):
    counts = {key: int(value) for key, value in re.findall(
        r'^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$', text, re.M)}
    if counts.get('tests', 0) < 4 or counts.get('pass') != counts.get('tests'):
        raise ValueError('JavaScript test run missing or incomplete')
    if any(counts.get(key, -1) != 0 for key in ('fail', 'cancelled', 'skipped', 'todo')):
        raise ValueError('JavaScript suite contains non-passing outcomes')


def redact(data, values):
    for value in values:
        data = data.replace(value, b'[REDACTED]')
    return data


class CI:
    def __init__(self, state, artifacts):
        self.state = state.resolve()
        self.artifacts = artifacts.resolve()
        self.env_file = self.state / 'runtime.env'
        self.raw = self.state / 'raw'
        self.project = os.environ['COMPOSE_PROJECT_NAME']
        if not re.fullmatch(r'kc-hw05-ci-[a-z0-9-]+', self.project):
            raise ValueError('use a new kc-hw05-ci-* project')
        self.env = os.environ.copy()
        # All runtime settings come from the generated CI file, not host bindings.
        for name in SECRET_NAMES | {'KAFKA_BOOTSTRAP_SERVERS', 'WEATHER_NETWORK_ENABLED',
                                    'DJANGO_DEBUG', 'DJANGO_ALLOWED_HOSTS', 'KAZAN_HTTP_PORT'}:
            self.env.pop(name, None)
        self.env.update(KAZAN_HTTP_PORT='18005', WEATHER_NETWORK_ENABLED='0',
                        KAFKA_BOOTSTRAP_SERVERS='kafka:9092')
        self.dc = ['docker', 'compose', '-p', self.project, '--env-file', str(self.env_file)]

    def record(self, name, status):
        self.artifacts.mkdir(parents=True, exist_ok=True)
        with (self.artifacts / 'summary.txt').open('a') as output:
            output.write(f'{status} {name}\n')
        print(f'{status} {name}', flush=True)

    def command(self, name, argv, env=None, timeout=240):
        self.raw.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.raw / (name + '.log')).open('wb') as output:
            result = subprocess.run(argv, cwd=ROOT, env=env or self.env, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            self.record(name, 'FAIL')
            raise RuntimeError('command failed; see audited artifact logs')
        self.record(name, 'PASS')

    def prepare(self):
        if self.state.exists() or self.artifacts.exists():
            raise ValueError('CI state/artifacts must be new directories')
        # Refuse existing project resources BEFORE issuing any mutating command.
        for kind in ('container', 'volume', 'network'):
            result = subprocess.check_output(['docker', kind, 'ls', '-q', '--filter',
                f'label=com.docker.compose.project={self.project}'], env=self.env)
            if result.strip():
                raise ValueError('project already owns Docker resources; refusing reuse')
        existing_images = subprocess.check_output(['docker', 'image', 'ls', '-q', '--filter',
            f'reference={self.project}-*'], env=self.env)
        if existing_images.strip():
            raise ValueError('project already has images; refusing reuse')
        self.state.mkdir(parents=True, mode=0o700)
        self.raw.mkdir(mode=0o700)
        (self.state / 'owner.json').write_text(json.dumps({'project': self.project}))
        self.command('generate-env', [sys.executable, 'scripts/init_local_env.py', '--output', str(self.env_file)])
        self.command('compose-config', self.dc + ['config', '--quiet'])

    def run(self):
        self.command('build', self.dc + ['build', '--pull', '--no-cache'], timeout=900)
        self.command('start', self.dc + ['up', '-d', '--wait', '--wait-timeout', '180'], timeout=300)
        self.command('services', self.dc + ['ps', '--all', '--format', 'json'])
        items = (self.raw / 'services.log').read_text()
        try:
            states = json.loads(items)
        except json.JSONDecodeError:
            states = [json.loads(line) for line in items.splitlines() if line.strip()]
        by_name = {row['Service']: row for row in states}
        if any(by_name.get(name, {}).get('State') != 'running' for name in
               ('web', 'db', 'kafka', 'scheduler', 'publisher', 'consumer')):
            raise ValueError('required Compose service is missing/stopped')
        if by_name.get('migrate', {}).get('ExitCode') != 0:
            raise ValueError('migration job did not complete successfully')
        self.record('all-services-and-migrations', 'PASS')
        self.command('django-check', self.dc + ['exec', '-T', 'web', 'python', 'manage.py', 'check'])
        self.command('migrations', self.dc + ['exec', '-T', 'web', 'python', 'manage.py', 'migrate', '--check'])
        self.command('model-drift', self.dc + ['exec', '-T', 'web', 'python', 'manage.py', 'makemigrations', '--check', '--dry-run'])
        self.command('seed-before', self.dc + ['exec', '-T', 'web', 'python', 'manage.py', 'seed_demo_data'])
        self.command('health-failure-recovery', [sys.executable, 'scripts/check_production.py',
            '--project', self.project, '--port', '18005', '--env-file', str(self.env_file)], timeout=480)
        self.command('seed-after', self.dc + ['exec', '-T', 'web', 'python', 'manage.py', 'seed_demo_data'])
        if '0 courts and 0 customers created' not in (self.raw / 'seed-after.log').read_text():
            raise ValueError('demo data did not survive retained-volume restart')
        self.command('pause-workers', self.dc + ['stop', 'scheduler', 'publisher', 'consumer'])
        bindings = dict(line.split('=', 1) for line in self.env_file.read_text().splitlines()
                        if '=' in line and not line.startswith('#'))
        env = self.env.copy()
        env['DB_PASSWORD'] = bindings['TEST_DB_PASSWORD']
        self.command('django-tests', self.dc + ['exec', '-T', '-e', 'DB_NAME=postgres', '-e', 'DB_USER=kazan_test',
            '-e', 'DB_PASSWORD', '-e', 'KAFKA_INTEGRATION=1', '-e', 'KAZAN_TEST_RESULTS=/tmp/ci-tests.json',
            'web', 'python', 'manage.py', 'test', 'test_database', 'bookings', 'config.test_health',
            '--testrunner=config.ci_runner.CIResultRunner', '--verbosity', '2', '--noinput'], env=env)
        self.command('collect-django-results', self.dc + ['cp', 'web:/tmp/ci-tests.json', str(self.raw / 'django-results.json')])
        validate_django(json.loads((self.raw / 'django-results.json').read_text()))
        self.record('django-counts-no-skips-and-real-kafka', 'PASS')
        self.command('js-tests', ['node', '--test', '--test-reporter=tap', 'bookings/tests/operator_queue_ui.test.js'])
        validate_js((self.raw / 'js-tests.log').read_text())
        self.record('javascript-counts-no-skips', 'PASS')
        self.command('post-tests-db', self.dc + ['exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c',
            "from django.db import connection; c=connection.cursor(); c.execute(\"SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='test_kazan_courts'), has_database_privilege('kazan_test','kazan_courts_dev','CONNECT')\"); assert c.fetchone()==(False,False); print('PASS isolated test DB removed and dev connection denied')"])
        self.record('test-phase', 'PASS')

    def cleanup_local_images(self):
        # Compose --rmi local can retain tagged build images on some Docker
        # versions. Remove only tags in our previously unused project namespace;
        # never force removal or remove a shared base by image ID.
        output = subprocess.check_output(['docker', 'image', 'ls', '--format',
            '{{.Repository}}:{{.Tag}}', '--filter', f'reference={self.project}-*'],
            env=self.env, text=True)
        refs = sorted(set(output.split()))
        if any(not ref.startswith(self.project + '-') or '@' in ref for ref in refs):
            raise ValueError('refusing image outside this CI project')
        if refs:
            self.command('cleanup-local-image-tags', ['docker', 'image', 'rm', *refs])

    def finalize(self):
        owner = self.state / 'owner.json'
        if not owner.exists() or json.loads(owner.read_text())['project'] != self.project:
            self.record('cleanup-not-authorized-no-owner', 'FAIL')
            return 1
        failed = False
        try:
            self.command('service-logs', self.dc + ['logs', '--no-color', '--tail', '300'])
            image_text = subprocess.check_output(self.dc + ['images', '-q'], cwd=ROOT, env=self.env, text=True)
            images = sorted(set(image_text.split()))
            if not images:
                raise ValueError('no images available for leak audit')
            argv = [sys.executable, 'scripts/audit_local_secrets.py', '--env-file', str(self.env_file), '--logs', str(self.raw)]
            for image in images:
                argv += ['--image', image]
            # Kafka is an unchanged upstream image. Its GnuTLS library contains
            # built-in PEM material. Scan every layer for our generated values;
            # generic signatures remain mandatory for code/logs/our own images.
            kafka_images = subprocess.check_output(self.dc + ['images', '-q', 'kafka'],
                cwd=ROOT, env=self.env, text=True).split()
            for image in kafka_images:
                argv += ['--temporary-secrets-only-image', image]
            self.command('secret-audit', argv, timeout=600)
        except Exception:
            failed = True
            self.record('secret-audit-or-collection', 'FAIL')
        try:
            self.command('cleanup', self.dc + ['down', '--volumes', '--remove-orphans', '--rmi', 'local'], timeout=180)
            self.cleanup_local_images()
            for kind in ('container', 'volume', 'network'):
                remaining = subprocess.check_output(['docker', kind, 'ls', '-q', '--filter',
                    f'label=com.docker.compose.project={self.project}'], env=self.env)
                if remaining.strip():
                    self.record('cleanup-' + kind + '-remaining-' + str(len(remaining.split())), 'FAIL')
                    raise ValueError('temporary project resource remains')
            if subprocess.check_output(['docker', 'image', 'ls', '-q', '--filter',
                    f'reference={self.project}-*'], env=self.env).strip():
                self.record('cleanup-image-tags-remain', 'FAIL')
                raise ValueError('temporary project image remains')
            self.record('cleanup-resources-verified', 'PASS')
        except Exception:
            failed = True
            self.record('cleanup-resources-verified', 'FAIL')
        # Only sanitized logs enter the upload directory; leak findings remain FAIL.
        values = [line.split('=', 1)[1].encode() for line in self.env_file.read_text().splitlines()
                  if line.partition('=')[0] in SECRET_NAMES] if self.env_file.exists() else []
        if len(values) != 4:
            failed = True
        else:
            for path in self.raw.iterdir():
                if path.is_file():
                    raw = path.read_bytes()
                    if any(value in raw for value in values):
                        failed = True
                        self.record('raw-result-secret-leak', 'FAIL')
                    safe = redact(raw, values)
                    # On signature/audit failure withhold all raw output, including
                    # unknown credential formats; retain only fixed status lines.
                    if not failed:
                        (self.artifacts / path.name).write_bytes(safe)
        if failed:
            for path in self.artifacts.iterdir():
                if path.name != 'summary.txt':
                    path.unlink()
            self.record('finalization', 'FAIL')
        else:
            self.record('finalization', 'PASS')
        self.env_file.unlink(missing_ok=True)
        shutil.rmtree(self.raw, ignore_errors=True)
        return int(failed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'run', 'finalize'])
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    try:
        ci = CI(args.state_dir, args.artifacts)
        result = getattr(ci, args.phase)()
        return result or 0
    except Exception as error:
        # Exception text/command args could include secrets: print type only.
        print(f'FAIL {args.phase} ({type(error).__name__}); raw output withheld', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
