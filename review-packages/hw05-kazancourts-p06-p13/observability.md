# P06–P10 — наблюдение KazanCourts

Реализация для локального Compose; без внешних collectors, аккаунтов или интеграций.

## Поток

HTTP `TraceMiddleware` → weather.transition → PostgreSQL Outbox → kafka.publish → Kafka headers → kafka.consume → notification.persist → PostgreSQL.
Каждый этап имеет собственный span_id, parent_span_id, trace_id, correlation_id, started_at, duration_ms, outcome/error_type. Это причинно связанная трасса с длительностями, а не только общий job_id.
Outbox хранит три технических поля контекста (миграция 0007). В Kafka добавлены `traceparent` и `x-correlation-id` headers; business JSON envelope/schema_version, topic, порядок, ack/offset, retry и idempotency не менялись. Старые записи без контекста получают новую трассу publisher; прежнюю HTTP-историю восстановить из них нельзя.

## P06: JSON logs

Python LogRecords Django и фоновых процессов форматируются JSON: timestamp UTC, level, service, event, logger, trace_id, span_id, correlation_id, error_type. Spans дополнительно содержат parent/span timing/outcome.
Исходные сообщения библиотек и тексты traceback не выгружаются: они могут содержать SQL, credentials и персональные данные. Остаются logger и тип ошибки; это ограничивает подробность диагностики. Штатный CLI output management commands и native Kafka/PostgreSQL logs не объявляются унифицированным JSON.
HTTP body, cookies, Authorization, query strings, имена/телефоны клиентов не собираются. Request ID ограничен длиной/символами; невалидный traceparent заменяется. Операторам следует передавать только непрозрачные correlation IDs.

## P07/P08: контекст и trace

`X-Request-ID` принимается/генерируется и возвращается клиенту. ContextVar изолирует запросы и сбрасывается после завершения. Контекст Outbox сохраняется в той же транзакции, что событие; Kafka headers несут текущий publisher span. Consumer подтверждает offset после прежней операции записи результата. Повторная доставка получает новый span, сохраняя trace/correlation.
Наблюдение не меняет результат бизнес-операции при отказе collector: отправка best-effort с timeout 0.25s; structured stdout остаётся. Это не гарантированная доставка telemetry; недоступность monitoring не входит в business readiness.
Реальная трасса проверяется сетевым HTTP-запросом к WSGI на loopback, затем отдельными publisher/consumer процессами и настоящими PostgreSQL/Kafka. Только погодный адаптер заменён синтетическим прогнозом; сеть погоды выключена. Test fixture создаёт outdoor booking напрямую для проверки погодного pipeline, а не доказывает применимость правил создания такой брони в текущем сезоне. Регрессии доменных правил выполняются отдельно.

## P09: метрики

Collector — `python -m config.telemetry_server` в собственном Compose-сервисе `monitoring`. Хранилище SQLite **только для telemetry**, бизнес-БД остаётся PostgreSQL.

| Метрика | Источник | Единица/смысл |
|---|---|---|
| kazan_operations_total{service,event,outcome} | Принятые spans | Счётчик завершённых попыток HTTP/weather/publish/consume/persist |
| kazan_operation_duration_seconds_sum{service,event,outcome} | duration_ms spans | Сумма длительностей в секундах |
| Average duration на dashboard | duration/count | Средняя длительность попытки в секундах |

Идентификаторы запросов/броней не являются metric labels. Labels ограничены фиксированными service/event/outcome наборами. Spans дедуплицируются по span_id, ограничены 24 часами/50000 строк. Повторы одного бизнес-события с новыми spans считаются отдельными попытками; счётчики не заменяют уникальные бизнес-результаты. Счётчики сохраняются в volume collector, сбрасываются при его потере; при отказе collector события могут быть потеряны. Задержки отдельных spans не включают всю очередь ожидания; started_at позволяет увидеть паузы между этапами на одном облачном хосте.

## P10: dashboard

`127.0.0.1:${KAZAN_MONITOR_PORT:-19006}`: обновление таблицы каждые 3 секунды, выбор последних trace IDs и просмотр всех этапов/parent IDs/длительностей.
`/api/metrics`, `/api/traces?trace_id=...` и Prometheus text `/metrics` показывают те же фактические данные.
Сервис доступен только на loopback хоста; внутренний ingestion `/v1/spans` доверяет локальной Compose-сети и не имеет internet-facing authentication. Не публиковать его наружу. Пользовательские строки вставляются через textContent, не HTML.
Данные monitor сохраняются в `compose_observability_data`. Они диагностические и не заменяют backup бизнес-БД; при аварийном восстановлении допускается новый collector с потерей прежней telemetry-истории.
