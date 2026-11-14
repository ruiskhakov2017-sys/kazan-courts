"""Verify that CI evidence gates reject misleading successful process exits."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci_production import CI, KAFKA_TESTS, redact, validate_django, validate_js


class EvidenceTests(unittest.TestCase):
    def good(self):
        return dict(discovered_ids=sorted(KAFKA_TESTS) + [f'test.case_{i}' for i in range(219)],
                    tests_run=221, failures=0, errors=0, skipped=0, expected_failures=0,
                    unexpected_successes=0)

    def test_complete_suite_passes(self):
        validate_django(self.good())

    def test_zero_test_and_incomplete_suites_fail(self):
        for count in (0, 220, 222):
            data = self.good()
            data['tests_run'] = count
            with self.subTest(count=count), self.assertRaises(ValueError):
                validate_django(data)

    def test_nonpassing_outcomes_fail_even_with_exit_zero(self):
        for field in ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'):
            data = self.good()
            data[field] = 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_django(data)

    def test_missing_real_kafka_and_duplicate_discovery_fail(self):
        for replacement in ('different.test', 'test.case_0'):
            data = self.good()
            data['discovered_ids'][0] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                validate_django(data)

    def test_new_tests_are_counted_automatically(self):
        data = self.good()
        data['discovered_ids'].append('new.actual.test')
        data['tests_run'] += 1
        validate_django(data)

    def test_js_requires_all_four_actual_successes(self):
        good = '# tests 4\n# pass 4\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n'
        validate_js(good)
        for text in ('', good.replace('tests 4', 'tests 0'), good.replace('pass 4', 'pass 3'),
                     good.replace('skipped 0', 'skipped 1'), good.replace('# todo 0\n', '')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_js(text)

    def test_redaction_removes_all_occurrences(self):
        value = b'synthetic-only-not-a-credential'
        self.assertNotIn(value, redact(value + b' ' + value, [value]))

    def test_host_external_bindings_cannot_enable_weather_or_remote_broker(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',
                COMPOSE_PROJECT_NAME='kc-hw05-ci-safety', WEATHER_NETWORK_ENABLED='1',
                KAFKA_BOOTSTRAP_SERVERS='external.invalid:9092'):
            ci = CI(Path(tmp) / 'state', Path(tmp) / 'results')
            self.assertEqual(ci.env['WEATHER_NETWORK_ENABLED'], '0')
            self.assertEqual(ci.env['KAFKA_BOOTSTRAP_SERVERS'], 'kafka:9092')

    def test_refuse_existing_project_resources_before_creating_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', COMPOSE_PROJECT_NAME='kc-hw05-ci-collision'):
            ci = CI(Path(tmp) / 'state', Path(tmp) / 'results')
            with patch('ci_production.subprocess.check_output', return_value=b'existing-volume\n'):
                with self.assertRaises(ValueError):
                    ci.prepare()
            self.assertFalse(ci.state.exists())

    def test_no_owner_never_runs_docker_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', COMPOSE_PROJECT_NAME='kc-hw05-ci-test'):
            ci = CI(Path(tmp) / 'state', Path(tmp) / 'results')
            with patch('ci_production.subprocess.run') as command:
                self.assertEqual(ci.finalize(), 1)
                command.assert_not_called()

    def test_upstream_image_scope_still_rejects_temporary_secret(self):
        import contextlib
        import io
        import runpy
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / 'runtime.env'
            names = ('POSTGRES_PASSWORD', 'DEV_DB_PASSWORD', 'TEST_DB_PASSWORD', 'DJANGO_SECRET_KEY')
            value = b'synthetic-only-generated-temporary-value'
            env.write_text('\n'.join(f'{name}={value.decode()}' for name in names))
            def output(argv, **kwargs):
                if argv[:2] == ['git', 'ls-files'] or argv[:2] == ['git', 'diff']:
                    return b''
                return b'fake-id' if '--format' in argv else value
            with patch('sys.argv', ['audit', '--env-file', str(env), '--logs', tmp,
                    '--image', 'upstream', '--temporary-secrets-only-image', 'upstream']), \
                    patch('subprocess.check_output', side_effect=output), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(Path(__file__).with_name('audit_local_secrets.py')), run_name='__main__')
                self.assertNotIn(value.decode(), str(result.exception))
                self.assertIn('local credential', str(result.exception))

    def test_a_leak_fails_finalize_and_withholds_logs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', COMPOSE_PROJECT_NAME='kc-hw05-ci-leak'):
            ci = CI(Path(tmp) / 'state', Path(tmp) / 'results')
            ci.state.mkdir(); ci.raw.mkdir()
            import json
            (ci.state / 'owner.json').write_text(json.dumps({'project': ci.project}))
            ci.env_file.write_text('\n'.join(f'{name}={name}-synthetic-private-value' for name in
                ('POSTGRES_PASSWORD', 'DEV_DB_PASSWORD', 'TEST_DB_PASSWORD', 'DJANGO_SECRET_KEY')))
            leaked = b'POSTGRES_PASSWORD-synthetic-private-value'
            (ci.raw / 'leak.log').write_bytes(leaked)
            def read(argv, **kwargs):
                return 'fake-image\n' if 'images' in argv else b''
            with patch.object(ci, 'command'), patch('ci_production.subprocess.check_output', side_effect=read):
                self.assertEqual(ci.finalize(), 1)
            self.assertFalse(ci.env_file.exists())
            self.assertFalse(ci.raw.exists())
            self.assertEqual([p.name for p in ci.artifacts.iterdir()], ['summary.txt'])
            self.assertNotIn(leaked, (ci.artifacts / 'summary.txt').read_bytes())


if __name__ == '__main__':
    unittest.main()
