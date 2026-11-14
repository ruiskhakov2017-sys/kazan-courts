"""Machine-readable evidence for the isolated Compose CI suite."""
import json
import os
from pathlib import Path
from django.test.runner import DiscoverRunner


class CIResultRunner(DiscoverRunner):
    def run_suite(self, suite, **kwargs):
        def ids(item):
            if hasattr(item, 'id'):
                return [item.id()]
            return [name for child in item for name in ids(child)]
        self.discovered_ids = ids(suite)
        return super().run_suite(suite, **kwargs)

    def suite_result(self, suite, result, **kwargs):
        data = {
            'discovered_ids': self.discovered_ids,
            'tests_run': result.testsRun,
            'failures': len(result.failures),
            'errors': len(result.errors),
            'skipped': len(result.skipped),
            'expected_failures': len(result.expectedFailures),
            'unexpected_successes': len(result.unexpectedSuccesses),
        }
        Path(os.environ['KAZAN_TEST_RESULTS']).write_text(json.dumps(data), encoding='utf-8')
        return super().suite_result(suite, result, **kwargs)
