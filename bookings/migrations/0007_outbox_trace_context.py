from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('bookings','0006_weather_retry_after')]
    operations = [migrations.AddField('outboxmessage', name, models.CharField(max_length=size, blank=True, default=''))
                  for name,size in [('trace_id',32),('correlation_id',64),('parent_span_id',16)]]
