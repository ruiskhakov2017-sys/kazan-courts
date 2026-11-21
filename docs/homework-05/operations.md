# KazanCourts — запуск, обновление и восстановление (P13)

Все сценарии предназначены для локального учебного Compose. Внешний deployment, production credentials и работа с действующими данными этим документом не разрешаются. Тестовые операции выполняются только на новых проектах/томах.

## 1. Запуск

Нужны Docker Engine/Compose v2/BuildKit, Python 3, свободные loopback-порты 8000, 19006 (или значения из env), достаточное место для PostgreSQL/Kafka/backup. Получить одобренную версию приложения. `git status --porcelain` должен быть пустым; при чужих изменениях остановиться, не reset/stash.

```bash
python3 scripts/init_local_env.py  # только один раз, не перезаписывает .env
# Проверить настройки в .env приватно, без вывода значений в отчёт.
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 180
docker compose exec -T web python manage.py check
docker compose exec -T web python manage.py migrate --check
docker compose exec -T web python manage.py seed_demo_data
```

Compose поднимает PostgreSQL, Kafka, monitoring, применяет миграции до web/scheduler/publisher/consumer. В database/broker наружные порты не публикуются; web и dashboard — только loopback. Введите сотрудника через интерактивный create_employee, если нужен ручной вход; пароли не передавать в аргументах.

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
curl --fail http://127.0.0.1:19006/api/metrics
```

Ожидаются live200, ready200 с PostgreSQL/Kafka true, login200 и демоданные. Dashboard обновляется и показывает новые spans. WEATHER_NETWORK_ENABLED=0: реальная погода не запрашивается.
Файлы .env/секреты сохранять отдельно в приватном хранилище; init генератор не меняет пароли существующего PostgreSQL volume. Не выполнять `docker compose config` без `--quiet` и не публиковать inspect/env.

## 2. Обновление приложения с сохранением данных

Сначала проверить [границу Debian/Alpine](postgresql-volume-review.md). Старый Debian volume не подключать к Alpine; эта инструкция не переносит libc/major-version.
Для уже Alpine-стенда выбрать новую одобренную версию, сохранить текущий SHA/образы, конфигурацию и приватную резервную копию. В тестах исходное приложение P01–P05 (`ef412f7...`) с миграциями 0006 обновляется до 0007 и нового образа с сохранением контрольной брони.

1. Подготовить новую версию в чистой копии исходников, проверить diff, собрать образы. При неудачной сборке оставить старую версию работающей.
2. Остановить web и **все** writers (scheduler/publisher/consumer) для окна обслуживания, оставить DB/Kafka. Сделать backup по разделу 3. Не удалять volumes.
3. Сохранить прежнюю конфигурацию и подключить её к одобренной новой версии. Не генерировать новые пароли для сохранённого DB volume.
4. Поднять зависимости/monitoring и применить миграции новым образом, затем все Django-сервисы:

```bash
docker compose stop web scheduler publisher consumer
# Здесь — приватный backup; команды в разделе 3.
docker compose build
docker compose up -d --wait db kafka monitoring
docker compose run --rm -T --no-deps migrate python manage.py migrate --plan
docker compose run --rm -T --no-deps migrate python manage.py migrate --noinput
docker compose up -d --wait --wait-timeout 180
docker compose exec -T web python manage.py migrate --check
```

5. Проверить live/ready, контрольную старую бронь/историю и новую запись через API, состояние Outbox/notification и dashboard. Сохранить версии и результат.

Миграция 0007 добавляет NOT NULL технические поля с ORM defaults, а не обещает backward-compatible запись старым кодом. Не смешивать старые и новые writers. После миграции не обещается rollback простым возвратом образа. При ошибке остановить writers, сохранить диагностику и выбирать отдельное восстановление в новый стенд из предмиграционной копии под соответствующую версию. Автоматический откат данных не реализован и не проверен. Нулевой downtime/rolling update не заявляется.

## 3. Резервная копия

После остановки writers задать приватный каталог **вне checkout** и сохранить одобренный SHA/образы отдельно. Каталог и dump могут содержать session/auth/клиентские данные, даже если PostgreSQL role passwords в pg_dump не входят.

```bash
umask 077
KAZAN_BACKUP_DIR=/workspace/kazancourts-private-backups
mkdir -p "$KAZAN_BACKUP_DIR"
chmod 700 "$KAZAN_BACKUP_DIR"
set -a; . ./.env; set +a
export PGPASSWORD="$DEV_DB_PASSWORD"
docker compose exec -T -e PGPASSWORD db pg_dump -U kazan_dev -d kazan_courts_dev \
  --format=custom --no-owner --no-privileges > "$KAZAN_BACKUP_DIR/kazan.dump"
unset PGPASSWORD
sha256sum "$KAZAN_BACKUP_DIR/kazan.dump"
docker compose exec -T db pg_dump --version
```

Проверить exit code, версию сервера/инструмента, размер, формат и контрольные данные. Не загружать dump или .env в Git/публичный Review ZIP. [Фактический backup/restore](backup-restore.md).

## 4. Восстановление в новый отдельный стенд

Выбрать соответствующую dump версию приложения/схемы, сохранить исходные volumes. Для recovery здесь предусмотрены **отдельные** restore-db/restore-kafka/restore-web и workers, другие volumes и порт web18016.
Не переименовывать рабочие volumes и не выполнять DROP/restore поверх исходной БД. Остановиться, если restore-db уже содержит таблицы.

```bash
docker compose --profile recovery build restore-db restore-web restore-publisher restore-consumer restore-scheduler
docker compose --profile recovery up -d --wait restore-db restore-kafka monitoring
docker compose exec -T restore-db psql -U postgres -d kazan_courts_dev -tAc \
  "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
# Продолжать только при 0 и подтверждённом новом recovery volume.
set -a; . ./.env; set +a
export PGPASSWORD="$DEV_DB_PASSWORD"
docker compose exec -T -e PGPASSWORD restore-db pg_restore -U kazan_dev -d kazan_courts_dev \
  --exit-on-error --no-owner --no-privileges < "$KAZAN_BACKUP_DIR/kazan.dump"
unset PGPASSWORD
docker compose --profile recovery run --rm -T --no-deps restore-web python manage.py migrate --check
# Сначала сравнить контрольные записи/связи, схему и sequences с backup manifest.
docker compose --profile recovery run --rm -T --no-deps restore-web python manage.py reconcile_restored_outbox
docker compose --profile recovery up -d --wait --wait-timeout 180 \
  restore-web restore-publisher restore-consumer restore-scheduler
curl --fail http://127.0.0.1:18016/health/ready
```

`reconcile_restored_outbox` разрешён только при recovery mode и DB_HOST=restore-db: возвращает в очередь опубликованные Outbox без persisted notification. Это закрывает окно «Kafka ack был, но broker потерян до обработки». Уже завершённые notifications не перевыставляются. Payload, event_id и порядок Outbox сохраняются; возможная повторная доставка остаётся идемпотентной по прежней логике.
После старта проверить завершение этих событий, отсутствие дубликатов, контрольное чтение и создание новой брони/истории через восстановленное приложение. До этого восстановление не считать завершённым.

Что откуда берётся: PostgreSQL — из проверенного dump; код/образы/миграции — из соответствующей версии; конфигурация и секреты — из приватной сохранённой конфигурации; Kafka в этом сценарии новая и восстанавливает незавершённую работу из Outbox; monitoring может использовать сохранённый диагностический volume или начать счётчики заново. Медиа/бизнес-файлов в KazanCourts нет. Исторические Kafka records/offsets и telemetry-history этим dump не сохраняются; отдельное восстановление старого broker volume не проверялось.

## 5. Повторяемая изолированная проверка

`ci_production.py prepare/run/finalize` создаёт новый project с `kc-hw05-ci-*`, отказывается от существующих ресурсов, выполняет регрессии и P06–P13 proofs, проверяет временные секреты и убирает только созданные ресурсы. `check_full_production.py` требует marker владельца. Synthetic fixtures/администраторские команды не являются пользовательской бизнес-функцией.

GitHub workflow подготовлен к новым проверкам, фактические результаты новой версии GitHub находятся в [acceptance](acceptance.md). Codex evidence, backup metadata, реальная трасса, screenshot и подробные границы находятся в отчёте повторного Review. Публикация кода разрешена пользователем 21 ноября; deployment и работа с действующими данными не выполняются.
