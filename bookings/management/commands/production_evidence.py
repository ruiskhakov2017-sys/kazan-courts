"""Synthetic fixtures and read proofs; guarded, never a user-facing feature."""
from datetime import timedelta
import hashlib
import json
import os
import secrets
from threading import Thread
import time
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler
from wsgiref.simple_server import make_server, WSGIRequestHandler

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.wsgi import get_wsgi_application
from django.db import connection
from django.test import Client
from django.utils import timezone
from bookings.models import Booking, BookingEvent, Court, Customer, OutboxMessage, SimulatedNotification
from bookings.tests.weather_fixtures import weather_forecast
from bookings.weather_transitions import apply_weather_transition


def employee():
    return get_user_model().objects.get_or_create(username='synthetic-production-evidence',
        defaults={'is_staff':True, 'password':'!'})[0]


def request(base, path, actor, payload=None):
    client=Client();client.force_login(actor)
    csrf=secrets.token_hex(16)
    cookie=f"{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}; csrftoken={csrf}"
    headers={'Cookie':cookie,'X-CSRFToken':csrf,'X-Request-ID':'evidence-'+secrets.token_hex(8),
             'Content-Type':'application/json','Referer':base+'/'}
    req=Request(base+path,json.dumps(payload).encode() if payload is not None else None,headers)
    with build_opener(ProxyHandler({})).open(req,timeout=15) as response:
        return response.status,json.load(response),response.headers


class Quiet(WSGIRequestHandler):
    def log_message(self,*args):pass


class Command(BaseCommand):
    def add_arguments(self,parser):
        parser.add_argument('stage',choices=['http','pending','manifest','read','outcomes'])
        parser.add_argument('--booking-id',type=int)
        parser.add_argument('--base',default='http://127.0.0.1:8000')

    def handle(self,*args,**o):
        if (os.environ.get('KAZAN_VERIFICATION_ALLOWED') != '1' or
            settings.DATABASES['default']['HOST'] not in ('db','restore-db','upgrade-db') or settings.WEATHER_NETWORK_ENABLED):
            raise CommandError('Requires explicitly isolated verification DB and weather network disabled')
        stage=o['stage']
        if stage=='http':
            actor=employee(); customer=Customer.objects.create(name='Synthetic evidence',phone='+70000000000')
            court=Court.objects.create(name='Synthetic trace court',court_type='outdoor',latitude=55,longitude=49)
            start=(timezone.localtime()+timedelta(days=1)).replace(hour=12,minute=0,second=0,microsecond=0)
            booking=Booking.objects.create(court=court,customer=customer,created_by=actor,starts_at=start,
                ends_at=start+timedelta(hours=1),weather_status='clear')
            forecast=weather_forecast(timezone.now(),days=3,changes={start:61})
            # Physical loopback HTTP through the real WSGI/middleware/API stack;
            # only the forecast adapter is replaced with synthetic rain.
            with patch('bookings.services.fetch_forecast',return_value=forecast):
                server=make_server('127.0.0.1',0,get_wsgi_application(),handler_class=Quiet)
                thread=Thread(target=server.serve_forever,daemon=True);thread.start()
                try: status,body,headers=request(f'http://127.0.0.1:{server.server_port}',
                    '/api/weather/recheck/',actor,{})
                finally: server.shutdown();thread.join();server.server_close()
            assert status==200
            message=OutboxMessage.objects.get(booking_event__booking=booking)
            assert message.trace_id==headers['traceparent'].split('-')[1]
            data={'trace_id':message.trace_id,'correlation_id':message.correlation_id,
                  'booking_id':booking.pk,'event_id':str(message.event_id)}
        elif stage=='pending':
            booking=Booking.objects.get(pk=o['booking_id'])
            message=apply_weather_transition(booking_id=booking.pk,expected_starts_at=booking.starts_at,
                expected_ends_at=booking.ends_at,status='clear',checked_at=timezone.now())
            assert message is not None
            data={'event_id':str(message.event_id),'booking_id':booking.pk}
        elif stage=='outcomes':
            data={'notifications':SimulatedNotification.objects.count(),
                  'simulated':SimulatedNotification.objects.filter(result='simulated').count(),
                  'pending':OutboxMessage.objects.filter(published_at__isnull=True).count(),
                  'missing':OutboxMessage.objects.filter(notification__isnull=True).count()}
        elif stage=='manifest':
            digest=hashlib.sha256();counts={}
            with connection.cursor() as cursor:
                cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
                tables=[r[0] for r in cursor.fetchall()]
                for table in tables:
                    cursor.execute('SELECT row_to_json(t)::text FROM '+connection.ops.quote_name(table)+' t')
                    rows=sorted(r[0] for r in cursor.fetchall());counts[table]=len(rows)
                    digest.update(json.dumps([table,rows],ensure_ascii=False).encode())
                cursor.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
                for (seq,) in cursor.fetchall():
                    cursor.execute('SELECT last_value,is_called FROM '+connection.ops.quote_name(seq))
                    digest.update(json.dumps([seq,cursor.fetchone()]).encode())
            data={'sha256':digest.hexdigest(),'counts':counts,'database':connection.settings_dict['NAME']}
        else:
            booking=Booking.objects.get(pk=o['booking_id'])
            actor=employee(); date=booking.starts_at.astimezone(timezone.get_current_timezone()).date().isoformat()
            status,body,_=request(o['base'],f'/api/schedule/?court_id={booking.court_id}&date={date}',actor)
            assert status==200 and any(b['id']==booking.pk for b in body['bookings'])
            # Exercise a write on a separate indoor court, checking restored sequences.
            indoor=Court.objects.filter(court_type='indoor').first()
            start=(timezone.localtime()+timedelta(days=2)).replace(hour=15,minute=0,second=0,microsecond=0)
            status,created,_=request(o['base'],'/api/bookings/',actor,{'court_id':indoor.pk,
                'customer_id':booking.customer_id,'starts_at':start.isoformat(),'ends_at':(start+timedelta(hours=1)).isoformat()})
            assert status==201 and BookingEvent.objects.filter(booking_id=created['booking']['id'],event_type='created').exists()
            data={'read_booking_id':booking.pk,'new_booking_id':created['booking']['id'],'http_status':status}
        self.stdout.write('EVIDENCE '+json.dumps(data,sort_keys=True))
