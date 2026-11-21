import io
import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase
from config.telemetry import JSONFormatter, context, incoming, span, trace_fields, headers, from_headers
from config import telemetry_server as server


class ObservabilityTests(SimpleTestCase):
    def test_untrusted_ids_are_replaced_and_context_is_reset(self):
        with patch('config.telemetry.emit'):
            with span('http.request',incoming('bad','bad\nsecret')) as c:
                self.assertEqual(len(c['trace_id']),32)
                self.assertNotIn('\n',c['correlation_id'])
            self.assertEqual(context(),{})

    def test_parent_and_kafka_round_trip(self):
        with patch('config.telemetry.emit'):
            with span('http.request') as root:
                fields=trace_fields(); wire=headers()
                with span('kafka.consume',from_headers(wire)) as child:
                    self.assertEqual(child['trace_id'],root['trace_id'])
                    self.assertEqual(child['parent_span_id'],root['span_id'])
                    self.assertEqual(fields['correlation_id'],child['correlation_id'])

    def test_formatter_excludes_payload_secrets_and_exception_text(self):
        record=logging.LogRecord('bookings',logging.ERROR,'',1,'password=%s',('sensitive',),None)
        record.obs_span={'password':'sensitive'}
        text=JSONFormatter().format(record)
        self.assertNotIn('sensitive',text)
        self.assertNotIn('password',text)
        self.assertIn('timestamp',json.loads(text))

    def test_telemetry_exception_does_not_replace_business_result(self):
        with patch('config.telemetry.emit',side_effect=OSError):
            with span('http.request'):
                result=42
        self.assertEqual(result,42)

    def test_error_span_and_original_exception(self):
        with patch('config.telemetry.emit') as emit:
            with self.assertRaisesRegex(ValueError,'original'):
                with span('http.request'):
                    raise ValueError('original')
        self.assertEqual(emit.call_args.args[0]['outcome'],'error')
        self.assertNotIn('original',json.dumps(emit.call_args.args[0]))

    def test_collector_deduplicates_spans_and_rejects_arbitrary_labels(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server,'DB',str(Path(tmp)/'s.db')):
            server.initialize()
            with patch('config.telemetry.emit') as emit:
                with span('http.request'): pass
            data=emit.call_args.args[0];data['service']='web'
            server.store(data);server.store(data)
            with server.connect() as c:
                self.assertEqual(c.execute('SELECT count FROM metrics').fetchone()[0],1)
            data['service']='user-supplied-unbounded-label'
            with self.assertRaises(AssertionError):server.store(data)

    def test_request_id_and_live_remain_available(self):
        with patch('config.telemetry.emit') as emit:
            response=self.client.get('/health/live',HTTP_X_REQUEST_ID='review-request-1')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['X-Request-ID'],'review-request-1')
        self.assertEqual(emit.call_args.args[0]['event'],'http.request')
