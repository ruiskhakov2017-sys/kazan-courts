# KazanCourts — Production Practice P01–P13

21 ноября 2026. P01–P05 ранее вошли в main через PR19. P06–P13 реализованы и проверены в Codex в отдельной ветке `feature/hw05-kazancourts-p06-p13`, **пока без commit/push/PR**. Статус ниже — технические результаты для независимого Review; он не заменяет приёмку ментора/защиту и не закрывает автоматически CP5.
Источники: [Homework §5](sources/Mentor_Homework_005.md) и [roadmap](sources/ai_native_engineering_roadmap.md).

## Матрица фактического выполнения

| ID | Требование | Реализация / доказательство | Codex 21 ноября |
|---|---|---|---|
| P01 | Запуск всего сценария Compose | DB, Kafka, monitoring, migrate, web, scheduler, publisher, consumer; отдельные recovery/upgrade profiles | PASS |
| P02 | Environment | .env.example, генератор 0600/O_EXCL, порты и telemetry/service settings без бизнес-правок | PASS |
| P03 | Secrets вне Git/образов/отчётов | Целевой аудит временных значений всех образов/логов; generic signatures собственных образов; приватный dump исключён | PASS в заявленном scope |
| P04 | Liveness | Процесс отвечает при остановке DB/Kafka | PASS |
| P05 | Readiness | DB + Kafka, ready200→503→200 | PASS |
| P06 | Structured logs | JSON Python LogRecords, timestamp/level/service/event/error/trace context; реальные образцы в evidence | PASS |
| P07 | Correlation ID | HTTP response ID → сохранённый Outbox context → Kafka headers → consumer | PASS |
| P08 | Сквозная трасса | 5 связанных этапов с parent IDs и длительностями, реальные HTTP/PostgreSQL/Kafka/workers | PASS |
| P09 | Metrics | Counters/duration sum по bounded labels, рост после success и HTTP404 | PASS |
| P10 | Dashboard | Живая таблица + выбор traces, Prometheus text; browser rendering/screenshot | PASS |
| P11 | PostgreSQL backup | Реальный custom pg_dump18.6, checksum/размер/версия/контрольные данные | PASS |
| P12 | PostgreSQL restore | Чистая другая БД; совпали public rows/sequences; приложение читает/пишет, возобновлена незавершённая обработка | PASS |
| P13 | Запуск/обновление/восстановление | Три runbook-сценария; обновление реального P01 приложения/0006 до нового образа/0007, сохранение брони; отдельный recovery stack | PASS в локальном учебном сценарии |

Доказательства: [testing-report](testing-report.md), [observability](observability.md), [backup/restore](backup-restore.md), [operations](operations.md).
Скрипты воспроизведения: `ci_production.py`, `check_full_production.py`, guarded management commands `production_evidence` / `reconcile_restored_outbox`.

## Компоненты и постоянные данные

HTTP → Django/PostgreSQL; scheduler → weather transitions/Outbox; publisher → Kafka → consumer/PostgreSQL.
Дополнительный monitoring принимает безопасные spans, хранит telemetry SQLite в отдельном диагностическом volume и показывает dashboard. SQLite не заменяет PostgreSQL для бизнеса.
PostgreSQL Alpine использует отдельный `compose_postgres_alpine_data`; старый Debian volume не подключается. Recovery и upgrade используют **свои** DB volumes; recovery также свой свежий Kafka.
Monitoring/HTTP публикуются только на loopback; DB/Kafka не публикуются на хост. При отказе collector бизнес-операции не блокируются telemetry exception; доставку диагностических данных не гарантируем.

## Настройки и секреты

`.env` читает Compose, прямой Django сам его не загружает. Shell overrides имеют приоритет; не выводить полный config/inspect/environment в отчёт.

| Переменная | Назначение |
|---|---|
| POSTGRES_PASSWORD / DEV_DB_PASSWORD / TEST_DB_PASSWORD | Уникальные bootstrap/dev/test passwords; генератор не перезаписывает существующий файл |
| DJANGO_SECRET_KEY | Уникальный signing key, приватная конфигурация |
| DJANGO_DEBUG / DJANGO_ALLOWED_HOSTS | Локальный debug и допустимые hosts |
| DB_HOST/PORT/NAME/USER/PASSWORD | Compose назначает DB/роль; CI tests только postgres/kazan_test; recovery только restore-db |
| KAFKA_BOOTSTRAP_SERVERS | Локальный kafka:9092; recovery restore-kafka:9092 |
| WEATHER_NETWORK_ENABLED | 0; реальные погодные запросы не выполнялись |
| KAZAN_HTTP_PORT | Основной web, default8000; CI18005 |
| KAZAN_MONITOR_PORT | Dashboard, default19006 |
| KAZAN_RESTORE_PORT / KAZAN_UPGRADE_PORT | Recovery18016 / проверка upgrade18017 |
| TELEMETRY_ENDPOINT | Внутренний collector; прямой Django без настройки не отправляет spans |
| KAZAN_SERVICE | Имя компонента, задаёт Compose |
| KAZAN_RECOVERY_MODE | Только recovery stack, разрешает guarded reconciliation |
| KAZAN_VERIFICATION_ALLOWED | Только явные synthetic evidence commands; не пользовательская функция |

Secrets передаются через environment: администратор хоста/Docker может их видеть. Это не secret manager. Dump содержит приватные DB/session/auth данные, не включён в Git или публичный пакет. Generic signature audit unchanged upstream Kafka имеет явную границу GnuTLS fixtures; поиск всех временных CI-значений в нём сохраняется, как описано в [предыдущем CI review](ci-review.md).

## Границы готовности

Новая версия P06–P13 **ещё не проверена в GitHub Actions и не отправлена в GitHub**. Прежний hosted PASS относился к P01–P05 и 221 тесту, не ко всему новому этапу.
Нет внешнего deployment, HA, alerts, полной security certification или гарантии RPO/RTO. Не проверены реальный погодный provider, перенос Debian→Alpine существующих данных, zero-downtime/rolling update и автоматический rollback данных. Runserver сохранён для локального сценария.
Теория A/B/C и способность пользователя объяснить решения оцениваются отдельно. VidHarbor не изменялся и его статусы не подтверждаются этим отчётом.
