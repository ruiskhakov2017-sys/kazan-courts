"""P06-P13 proofs on a freshly owned CI project; never operate on an existing stack."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from urllib.request import urlopen
from ci_production import CI
ROOT = Path(__file__).resolve().parent.parent

p=argparse.ArgumentParser();p.add_argument('--state-dir',type=Path,required=True);p.add_argument('--artifacts',type=Path,required=True)
a=p.parse_args();ci=CI(a.state_dir,a.artifacts)
assert json.loads((ci.state/'owner.json').read_text())['project']==ci.project
recovery=ci.dc+['--profile','recovery','--profile','verification']
backup=ci.state/'backup';backup.mkdir(mode=0o700)
output=ci.raw


def call(name,argv,env=None,timeout=240):
    ci.command('full-'+name,argv,env=env,timeout=timeout)
    return (ci.raw/('full-'+name+'.log')).read_text()


def proof(stage,service='web',**kwargs):
    argv=ci.dc+['exec','-T','-e','KAZAN_VERIFICATION_ALLOWED=1',service,'python','manage.py','production_evidence',stage]
    for k,v in kwargs.items():argv+=['--'+k.replace('_','-'),str(v)]
    raw=call(stage+'-'+service,argv)
    return json.loads(next(line[9:] for line in raw.splitlines() if line.startswith('EVIDENCE ')))


def wait_for(fn,description,timeout=60):
    end=time.monotonic()+timeout
    while True:
        try:
            value=fn()
            if value:return value
        except (OSError,ValueError,AssertionError):pass
        if time.monotonic()>end:raise AssertionError(description)
        time.sleep(1)


def get(path):
    with urlopen('http://127.0.0.1:19006'+path,timeout=5) as r:return json.load(r)


# HTTP request -> durable Outbox -> real background publisher/Kafka/consumer.
# Django poison/redelivery tests intentionally leave records in their broker.
# This is a brand-new, ownership-checked CI project with an empty dev Outbox;
# clear only its test topic before starting the independent scenario.
call('clear-test-topic',ci.dc+['exec','-T','kafka','/opt/kafka/bin/kafka-topics.sh',
    '--bootstrap-server','localhost:9092','--delete','--if-exists','--topic','booking.weather.v1'])
wait_for(lambda: 'booking.weather.v1' not in call('topic-cleared',ci.dc+['exec','-T','kafka',
    '/opt/kafka/bin/kafka-topics.sh','--bootstrap-server','localhost:9092','--list']), 'test topic was not removed')
call('workers',ci.dc+['start','publisher','consumer','scheduler'])
start_metrics=get('/api/metrics')
fixture=proof('http')
wait_for(lambda: proof('outcomes')['simulated']>=1,'notification did not reach DB')
spans=wait_for(lambda: (lambda s:s if {'http.request','weather.transition','kafka.publish','kafka.consume','notification.persist'}<=set(x['event'] for x in s) else None)(get('/api/traces?trace_id='+fixture['trace_id'])),'trace is incomplete')
by_id={s['span_id']:s for s in spans}
assert len({s['correlation_id'] for s in spans})==1
chain=['http.request','weather.transition','kafka.publish','kafka.consume','notification.persist']
for parent,child in zip(chain,chain[1:]):
    left=next(s for s in spans if s['event']==parent)
    right=next(s for s in spans if s['event']==child)
    assert right['parent_span_id']==left['span_id'],(parent,child)
assert {'publisher','consumer'}<=set(s['service'] for s in spans)
assert all(s['duration_ms']>=0 for s in spans)
(output/'full-trace.json').write_text(json.dumps(spans,indent=2))
# Exercise a real HTTP error and confirm the metrics/dashboards are live.
from urllib.error import HTTPError
before_errors=sum(m['count'] for m in get('/api/metrics')['metrics'] if m['outcome']=='error')
try:
    urlopen('http://127.0.0.1:18005/missing-for-monitor-proof',timeout=5)
    raise AssertionError('expected HTTP404')
except HTTPError as error:
    assert error.code==404
end_metrics=get('/api/metrics')
assert sum(m['count'] for m in end_metrics['metrics'])>sum(m['count'] for m in start_metrics['metrics'])
assert sum(m['count'] for m in end_metrics['metrics'] if m['outcome']=='error')>before_errors
(output/'full-metrics.json').write_text(json.dumps(end_metrics,indent=2))
with urlopen('http://127.0.0.1:19006/metrics') as r:assert b'kazan_operations_total' in r.read()
with urlopen('http://127.0.0.1:19006/') as r:assert b'KazanCourts' in r.read()

call('source-reconcile-refused',ci.dc+['exec','-T','-e','KAZAN_RECOVERY_MODE=1','web','python','manage.py','shell','-c',
    "from django.core.management import call_command; from django.core.management.base import CommandError; exec(\"try:\\n call_command('reconcile_restored_outbox')\\nexcept CommandError:\\n print('PASS source DB reconciliation refused')\\nelse:\\n raise AssertionError('source DB must not be changed')\")"])
# Consistent backup, including an acknowledged event not yet handled by consumer.
call('pause',ci.dc+['stop','consumer','publisher','scheduler'])
pending=proof('pending',booking_id=fixture['booking_id'])
call('publish-window',ci.dc+['exec','-T','web','python','manage.py','shell','-c',
    'from bookings.kafka_pipeline import publish_pending; assert publish_pending()==1'])
assert proof('outcomes')['missing']==1
call('quiesce',ci.dc+['stop','web'])
# Manifest command runs an isolated one-off app because web is now stopped.
argv=ci.dc+['run','--rm','-T','--no-deps','-e','KAZAN_VERIFICATION_ALLOWED=1','web','python','manage.py','production_evidence','manifest']
raw=call('source-manifest',argv)
manifest=json.loads(next(line[9:] for line in raw.splitlines() if line.startswith('EVIDENCE ')))
source_env=dict(line.split('=',1) for line in ci.env_file.read_text().splitlines() if '=' in line and not line.startswith('#'))
env=ci.env.copy();env['PGPASSWORD']=source_env['DEV_DB_PASSWORD']
dump=backup/'kazan-test.dump'
with dump.open('xb') as f:
    dump.chmod(0o600)
    subprocess.run(ci.dc+['exec','-T','-e','PGPASSWORD','db','pg_dump','-U','kazan_dev','-d','kazan_courts_dev','-Fc','--no-owner','--no-privileges'],env=env,cwd=ROOT,stdout=f,check=True)
metadata={'format':'PostgreSQL custom','sha256':hashlib.sha256(dump.read_bytes()).hexdigest(),'bytes':dump.stat().st_size,
          'source':manifest,'control':fixture,'unfinished_event':pending['event_id']}
metadata['pg_dump_version']=call('dump-version',ci.dc+['exec','-T','db','pg_dump','--version']).strip()
metadata['server_version']=call('server-version',ci.dc+['exec','-T','db','psql','-U','postgres','-tAc','SELECT version()']).strip()
assert dump.read_bytes().startswith(b'PGDMP')

# Restore to another new PostgreSQL volume and a genuinely fresh Kafka broker.
call('recovery-images',recovery+['build','restore-db','restore-web','restore-publisher','restore-consumer','restore-scheduler','upgrade-db','upgrade-web'],timeout=600)
call('recovery-dependencies',recovery+['up','--build','-d','--wait','--wait-timeout','180','restore-db','restore-kafka'],timeout=360)
empty=call('restore-empty',ci.dc+['exec','-T','restore-db','psql','-U','postgres','-d','kazan_courts_dev','-tAc',"SELECT count(*) FROM pg_tables WHERE schemaname='public'"])
assert empty.strip()=='0','refusing restore into a nonempty DB'
with dump.open('rb') as f, (output/'full-pg-restore.log').open('wb') as log:
    subprocess.run(ci.dc+['exec','-T','-e','PGPASSWORD','restore-db','pg_restore','-U','kazan_dev','-d','kazan_courts_dev','--exit-on-error','--no-owner','--no-privileges'],cwd=Path(__file__).resolve().parent.parent,env=env,stdin=f,stdout=log,stderr=subprocess.STDOUT,check=True)
raw=call('restored-manifest',recovery+['run','--rm','-T','--no-deps','-e','KAZAN_VERIFICATION_ALLOWED=1','restore-web','python','manage.py','production_evidence','manifest'],timeout=360)
restored=json.loads(next(line[9:] for line in raw.splitlines() if line.startswith('EVIDENCE ')))
assert restored==manifest,'restored rows/schema/sequences differ'
call('restore-migrations',recovery+['run','--rm','-T','--no-deps','restore-web','python','manage.py','migrate','--check'])
call('reconcile',recovery+['run','--rm','-T','--no-deps','restore-web','python','manage.py','reconcile_restored_outbox'])
call('restore-system',recovery+['up','--build','-d','--wait','--wait-timeout','180','restore-web','restore-publisher','restore-consumer','restore-scheduler'],timeout=480)
wait_for(lambda: proof('outcomes','restore-web')['missing']==0,'restored work did not finish')
assert proof('outcomes','restore-web')['simulated']==2
call('reconcile-idempotent',recovery+['run','--rm','-T','--no-deps','restore-web','python','manage.py','reconcile_restored_outbox'])
assert proof('outcomes','restore-web')['simulated']==2
proof('read','restore-web',booking_id=fixture['booking_id'])
with urlopen('http://127.0.0.1:18016/health/ready') as r:assert r.status==200
metadata['restored']=restored;metadata['recovery']='PASS: requeued unfinished acknowledged event, read and new booking HTTP201'
(output/'full-backup-restore.json').write_text(json.dumps(metadata,indent=2))

# Actual application-version/schema update on a third isolated database.
call('upgrade-db',recovery+['up','--build','-d','--wait','--wait-timeout','180','upgrade-db'],timeout=360)
previous=backup/'previous-code';previous.mkdir()
archive=backup/'previous.tar'
base='ef412f7c87c694512eb365f2a31439a46656aebc'
with archive.open('wb') as f:subprocess.run(['git','archive',base],stdout=f,check=True)
with tarfile.open(archive) as t:t.extractall(previous,filter='data')
tag=ci.project+'-previous:latest'
build=['docker','build','-t',tag]
if os.environ.get('KAZAN_BUILD_CA'):build+=['--secret','id=cloud_ca,src='+os.environ['KAZAN_BUILD_CA']]
call('previous-image',build+[str(previous)],timeout=600)
env['DB_PASSWORD']=source_env['DEV_DB_PASSWORD']
old=['docker','run','--rm','--network',ci.project+'_default','--env-file',str(ci.env_file),'-e','DB_HOST=upgrade-db','-e','DB_PORT=5432','-e','DB_NAME=kazan_courts_dev','-e','DB_USER=kazan_dev','-e','DB_PASSWORD',tag,'python','manage.py']
call('previous-migrate',old+['migrate','--noinput'],env=env)
seed="""from django.contrib.auth import get_user_model
from bookings.models import Court,Customer,Booking
from django.utils import timezone
from datetime import timedelta
u=get_user_model().objects.create_user('synthetic-production-evidence',is_staff=True)
c=Court.objects.create(name='Synthetic upgrade',court_type='indoor',latitude=55,longitude=49)
p=Customer.objects.create(name='Synthetic upgrade',phone='+70000000000')
s=(timezone.localtime()+timedelta(days=1)).replace(hour=12,minute=0,second=0,microsecond=0)
b=Booking.objects.create(court=c,customer=p,created_by=u,starts_at=s,ends_at=s+timedelta(hours=1),weather_status='not_applicable')
print('CONTROL',b.pk)
"""
raw=call('previous-data',old+['shell','-c',seed],env=env)
control=int(next(line.split()[1] for line in raw.splitlines() if line.startswith('CONTROL ')))
call('upgrade-plan',recovery+['run','--rm','-T','--no-deps','upgrade-web','python','manage.py','migrate','--plan'])
call('upgrade-migrate',recovery+['run','--rm','-T','--no-deps','upgrade-web','python','manage.py','migrate','--noinput'])
call('upgrade-start',recovery+['up','--build','-d','--wait','--wait-timeout','180','upgrade-web'],timeout=360)
proof('read','upgrade-web',booking_id=control)
with urlopen('http://127.0.0.1:18017/health/ready') as r:assert r.status==200
(output/'full-upgrade.json').write_text(json.dumps({'source_app':base,'from_schema':'0006','to_schema':'0007',
    'control_booking':control,'target_image':subprocess.check_output(ci.dc+['images','-q','web'],cwd=ROOT,env=ci.env,text=True).strip(),
    'source_image':subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip(),
    'read_and_new_booking':'PASS','readiness':'200'},indent=2))
print('PASS P06-P13: real trace, live metrics, backup, clean restore/recovery, application/schema update')
