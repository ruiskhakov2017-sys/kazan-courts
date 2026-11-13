# Operations — этап 1

## Локальный запуск (P01–P05)

Нужны Docker Engine с Compose v2/BuildKit и Python 3 для создания локального файла.
Из корня существующего KazanCourts:

```bash
python3 scripts/init_local_env.py
# Только при первой настройке; существующий .env сохранять.
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 180
docker compose exec web python manage.py check
docker compose exec web python manage.py migrate --check
docker compose exec web python manage.py seed_demo_data
```

`migrate` обязан завершиться с exit 0; web стартует после него. Начальная готовность Kafka может занять время.
Внутренние HTTP-проверки на 127.0.0.1:8000: `/health/live` → 200, `/health/ready` → 200 с двумя true.
GET `/` → 302 на login; `/login/` → 200. Для ручного входа `docker compose exec web python manage.py create_employee` интерактивно; не вводить пароль в аргументы/отчёты.
Seed создаёт только синтетические корты/клиентов. Повторный запуск seed не дублирует их.
Статические файлы обслуживает прежний Django runserver при DEBUG=1. Это локальный учебный сценарий; production WSGI/TLS/прокси в этом этапе не вводятся.

```bash
docker compose restart
docker compose ps
# Снова проверить live/ready и login.
docker compose down
# Тома сохраняются. Не использовать down --volumes для ценных данных.
```

## Изолированная регрессия

Нельзя использовать development/production Kafka или БД. Новый checkout/worktree, собственный .env, свободный порт, отдельный project:

```bash
export COMPOSE_PROJECT_NAME=kc-hw05-test-local
export KAZAN_HTTP_PORT=18005
docker compose up --build -d --wait --wait-timeout 180
python3 scripts/check_production.py --project "$COMPOSE_PROJECT_NAME" --port "$KAZAN_HTTP_PORT"
# Smoke намеренно останавливает DB/Kafka и рестартует свой проект.
docker compose stop scheduler publisher consumer
set -a; . ./.env; set +a
docker compose exec -T -e DB_NAME=postgres -e DB_USER=kazan_test \
  -e DB_PASSWORD="$TEST_DB_PASSWORD" -e KAFKA_INTEGRATION=1 \
  web python manage.py test test_database bookings config.test_health --verbosity 2 --noinput
node --test bookings/tests/operator_queue_ui.test.js
docker compose start scheduler publisher consumer
```

Интеграционные тесты пишут в Kafka topic этого стенда, поэтому после них не переносить этот volume в рабочую среду.
Тестовый пользователь не имеет CONNECT к development DB. Django создаёт/удаляет test_kazan_courts.
Перед удалением тестовых томов проверить, что project действительно выделен только этой проверке.

## Обновление системы

P13: **Not Run / не реализовано на этом этапе**. Не применять этот документ как production runbook обновления или rollback.

## Восстановление системы

P13: **Not Run / не реализовано на этом этапе**. См. [backup-restore](backup-restore.md); дамп и restore не проверены.
