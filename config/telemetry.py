"""Bounded, best-effort observability. No request bodies, credentials or SQL text."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import os
import re
import time
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler, Request
import uuid

_context = ContextVar('kazan_trace', default={})
HEX32 = re.compile(r'^[0-9a-f]{32}$')
HEX16 = re.compile(r'^[0-9a-f]{16}$')
SAFE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
logger = logging.getLogger('kazan.telemetry')


def context():
    return _context.get().copy()


def incoming(traceparent='', correlation_id=''):
    parts = traceparent.split('-') if isinstance(traceparent, str) else []
    valid = (len(parts) == 4 and parts[0] == '00' and HEX32.fullmatch(parts[1]) and
             HEX16.fullmatch(parts[2]) and re.fullmatch('[0-9a-f]{2}',parts[3]) and parts[1] != '0'*32 and parts[2] != '0'*16)
    return {'trace_id': parts[1] if valid else uuid.uuid4().hex,
            'parent_span_id': parts[2] if valid else '',
            'correlation_id': correlation_id if isinstance(correlation_id, str) and SAFE.fullmatch(correlation_id) else uuid.uuid4().hex}


def trace_fields():
    c = context()
    return {'trace_id': c.get('trace_id', ''), 'correlation_id': c.get('correlation_id', ''),
            'parent_span_id': c.get('span_id', '')}


def headers():
    c = context()
    return [('traceparent', f"00-{c['trace_id']}-{c['span_id']}-01".encode()),
            ('x-correlation-id', c['correlation_id'].encode())]


def from_headers(items):
    try:
        values = dict(items)
        return incoming(values.get('traceparent', b'').decode(), values.get('x-correlation-id', b'').decode())
    except (TypeError, ValueError, UnicodeError, AttributeError):
        return incoming()


class JSONFormatter(logging.Formatter):
    def format(self, record):
        c = context()
        data = {'timestamp': datetime.now(timezone.utc).isoformat(), 'level': record.levelname,
                'service': os.environ.get('KAZAN_SERVICE', 'web'),
                'event': getattr(record, 'obs_event', 'library.log'),
                'logger': record.name, 'trace_id': c.get('trace_id', ''),
                'span_id': c.get('span_id', ''), 'correlation_id': c.get('correlation_id', ''),
                'error_type': record.exc_info[0].__name__ if record.exc_info else ''}
        # Deliberately omit getMessage()/traceback: third-party exceptions can
        # contain passwords, customer details, URLs and SQL parameters.
        if hasattr(record, 'obs_span'):
            data.update({k:v for k,v in record.obs_span.items() if k in {
                'event','service','trace_id','span_id','parent_span_id','correlation_id',
                'started_at','duration_ms','outcome','error_type'}})
        return json.dumps(data, ensure_ascii=False, allow_nan=False)


def emit(data):
    logger.log(logging.ERROR if data['outcome']=='error' else logging.INFO, 'span', extra={'obs_event': data['event'], 'obs_span': data})
    endpoint = os.environ.get('TELEMETRY_ENDPOINT', '')
    parsed = urlparse(endpoint)
    if not endpoint or parsed.scheme != 'http' or parsed.hostname not in ('monitoring', '127.0.0.1', 'localhost'):
        return
    try:
        # This is the local Compose collector, never an external destination.
        request = Request(endpoint, json.dumps(data).encode(), {'Content-Type': 'application/json'})
        with build_opener(ProxyHandler({})).open(request, timeout=0.25) as response:
            response.read(1)
    except Exception:
        logger.warning('collector unavailable', extra={'obs_event': 'telemetry.unavailable'})


@contextmanager
def span(event, parent=None):
    old = context()
    c = (parent or old or incoming()).copy()
    if parent:
        parent_id = c.get('parent_span_id', '')
    else:
        parent_id = old.get('span_id', '')
    c.update(span_id=uuid.uuid4().hex[:16], parent_span_id=parent_id)
    token = _context.set(c)
    started = time.time()
    monotonic = time.monotonic()
    outcome, error = 'success', ''
    try:
        yield c
        outcome = c.get('outcome', 'success')
    except BaseException as exception:
        outcome, error = 'error', type(exception).__name__
        raise
    finally:
        try:
            emit({'event': event, 'service': os.environ.get('KAZAN_SERVICE', 'web'),
                  'trace_id': c['trace_id'], 'span_id': c['span_id'], 'parent_span_id': parent_id,
                  'correlation_id': c['correlation_id'], 'started_at': started,
                  'duration_ms': round((time.monotonic()-monotonic)*1000, 3),
                  'outcome': outcome, 'error_type': error})
        except Exception:
            logger.warning('telemetry dropped', extra={'obs_event': 'telemetry.dropped'})
        finally:
            _context.reset(token)


def observed(event):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(event):
                return function(*args, **kwargs)
        return wrapped
    return decorate


class TraceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        parent = incoming(request.headers.get('traceparent', ''), request.headers.get('X-Request-ID', ''))
        with span('http.request', parent) as c:
            response = self.get_response(request)
            if response.status_code >= 400:
                c['outcome'] = 'error'
            response['X-Request-ID'] = c['correlation_id']
            response['traceparent'] = f"00-{c['trace_id']}-{c['span_id']}-01"
            return response
