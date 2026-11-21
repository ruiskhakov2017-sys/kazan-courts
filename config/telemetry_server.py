"""Small local collector/dashboard. SQLite is telemetry only, not application DB."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlparse, parse_qs

DB = os.environ.get('TELEMETRY_DB', '/var/lib/kazan-observability/telemetry.sqlite3')
SERVICES = {'web','migrate','publisher','consumer','scheduler','verification',
            'restore-web','restore-publisher','restore-consumer','restore-scheduler'}
EVENTS = {'http.request','weather.cycle','weather.transition','kafka.publish','kafka.consume','notification.persist'}


def connect():
    c = sqlite3.connect(DB, timeout=2)
    c.row_factory = sqlite3.Row
    return c


def initialize():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS spans (span_id TEXT PRIMARY KEY, trace_id TEXT, body TEXT, created REAL)')
        c.execute('CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id)')
        c.execute('CREATE TABLE IF NOT EXISTS metrics (service TEXT, event TEXT, outcome TEXT, count INTEGER, duration REAL, PRIMARY KEY(service,event,outcome))')


def validate(d):
    assert set(d) == {'event','service','trace_id','span_id','parent_span_id','correlation_id',
                      'started_at','duration_ms','outcome','error_type'}
    assert d['service'] in SERVICES and d['event'] in EVENTS
    assert d['outcome'] in ('success','error')
    for key, size in [('trace_id',32),('span_id',16)]:
        assert re.fullmatch('[0-9a-f]{'+str(size)+'}',d[key]) and d[key] != '0'*size
    assert not d['parent_span_id'] or re.fullmatch('[0-9a-f]{16}',d['parent_span_id'])
    for key in ['correlation_id','error_type']:
        assert re.fullmatch('[A-Za-z0-9_.-]{0,64}',d[key])
    assert isinstance(d['started_at'],(float,int)) and math.isfinite(d['started_at'])
    assert isinstance(d['duration_ms'],(float,int)) and math.isfinite(d['duration_ms']) and d['duration_ms'] >= 0


def store(d):
    validate(d)
    with connect() as c:
        inserted = c.execute('INSERT OR IGNORE INTO spans VALUES (?,?,?,?)',
                            (d['span_id'],d['trace_id'],json.dumps(d),time.time())).rowcount
        if inserted:
            c.execute('INSERT INTO metrics VALUES (?,?,?,?,?) ON CONFLICT(service,event,outcome) DO UPDATE SET count=count+1,duration=duration+excluded.duration',
                      (d['service'],d['event'],d['outcome'],1,d['duration_ms']/1000))
        c.execute('DELETE FROM spans WHERE created < ?', (time.time()-86400,))
        c.execute('DELETE FROM spans WHERE rowid IN (SELECT rowid FROM spans ORDER BY created DESC LIMIT -1 OFFSET 50000)')


PAGE = '''<!doctype html><html lang="ru"><meta charset="utf-8"><title>KazanCourts monitoring</title>
<style>body{font:16px system-ui;background:#111827;color:#e5e7eb;margin:32px}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #374151;text-align:left}a{color:#67e8f9}pre{white-space:pre-wrap}input,button{padding:10px;margin:8px}</style>
<h1>KazanCourts — Monitoring</h1><p>Обновление каждые 3 секунды. Счётчики доставленных telemetry spans; длительности в секундах.</p>
<table><thead><tr><th>Сервис</th><th>Операция</th><th>Результат</th><th>Количество</th><th>Средняя длительность, s</th></tr></thead><tbody id="metrics"></tbody></table>
<h2>Сквозная трасса</h2><input id="trace" placeholder="trace_id"><button id="show">Показать</button><pre id="spans"></pre>
<h2>Последние трассы</h2><div id="recent"></div><p><a href="/metrics">Prometheus metrics</a></p>
<script>
async function trace(id){document.querySelector('#trace').value=id;const r=await fetch('/api/traces?trace_id='+encodeURIComponent(id));const d=await r.json();document.querySelector('#spans').textContent=d.map(s=>`${s.service}: ${s.event} [${s.outcome}] ${s.duration_ms}ms | span=${s.span_id} parent=${s.parent_span_id||'root'}`).join('\\n');}
document.querySelector('#show').onclick=()=>trace(document.querySelector('#trace').value);
async function refresh(){const d=await(await fetch('/api/metrics')).json();document.querySelector('#metrics').replaceChildren(...d.metrics.map(m=>{const tr=document.createElement('tr');for(const v of [m.service,m.event,m.outcome,m.count,(m.duration/m.count).toFixed(6)]){const td=document.createElement('td');td.textContent=v;tr.append(td);}return tr;}));document.querySelector('#recent').replaceChildren(...d.recent.map(id=>{const a=document.createElement('button');a.textContent=id;a.onclick=()=>trace(id);return a;}));}refresh();setInterval(refresh,3000);
</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body, content_type='application/json'):
        raw = body.encode() if isinstance(body,str) else json.dumps(body).encode()
        self.send_response(status); self.send_header('Content-Type',content_type)
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            with connect() as c: c.execute('SELECT 1').fetchone()
            return self.reply(200,{'status':'live'})
        if parsed.path == '/': return self.reply(200,PAGE,'text/html; charset=utf-8')
        if parsed.path == '/api/traces':
            trace = parse_qs(parsed.query).get('trace_id',[''])[0]
            if not re.fullmatch('[0-9a-f]{32}',trace): return self.reply(400,{'error':'invalid_trace_id'})
            with connect() as c:
                rows=c.execute('SELECT body FROM spans WHERE trace_id=? ORDER BY created',(trace,)).fetchall()
            return self.reply(200,[json.loads(r[0]) for r in rows])
        with connect() as c:
            metrics=[dict(row) for row in c.execute('SELECT * FROM metrics ORDER BY service,event,outcome')]
            recent=[r[0] for r in c.execute('SELECT trace_id FROM spans GROUP BY trace_id ORDER BY MAX(created) DESC LIMIT 12')]
        if parsed.path == '/api/metrics': return self.reply(200,{'metrics':metrics,'recent':recent})
        if parsed.path == '/metrics':
            text='# TYPE kazan_operations_total counter\n# TYPE kazan_operation_duration_seconds_sum counter\n'
            for m in metrics:
                labels=','.join(f'{k}="{m[k]}"' for k in ['service','event','outcome'])
                text+=f'kazan_operations_total{{{labels}}} {m["count"]}\n'
                text+=f'kazan_operation_duration_seconds_sum{{{labels}}} {m["duration"]}\n'
            return self.reply(200,text,'text/plain; version=0.0.4')
        self.reply(404,{'error':'not_found'})

    def do_POST(self):
        if self.path != '/v1/spans': return self.reply(404,{'error':'not_found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 4096: return self.reply(413,{'error':'size'})
            store(json.loads(self.rfile.read(size)))
        except (ValueError,TypeError,AssertionError,KeyError):
            return self.reply(400,{'error':'invalid_span'})
        self.reply(202,{'accepted':True})


if __name__ == '__main__':
    initialize()
    ThreadingHTTPServer(('0.0.0.0',9000),Handler).serve_forever()
