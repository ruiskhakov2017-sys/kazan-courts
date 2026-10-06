# Отчёт о проверках KazanCourts

## Этап 03 — структура данных, 5 октября 2026

Рабочая ветка: `task/03-data-models`, создана от актуальной чистой `main`
(`69c7d5e6a97ad4e8776c4b66e6fceb9a924bb737`). Результаты ниже относятся
к локальной реализации этапа 03; GitHub Actions для этих изменений ещё не запускался.

### Окружение и подготовка

Compose: `web` на Python 3.14.8, Django 5.2.17, psycopg 3.3.6;
`db` на PostgreSQL 18.6, отдельный volume `kazan-courts_compose_postgres_data`.
Секреты восстановлены из существующих локальных контейнеров только в окружение
процесса; их значения не записывались в проект и не выводились.

Первая сборка остановилась на TLS handshake timeout при чтении Docker Hub.
Повторная сборка прошла. Стек и настройки сети для этого не менялись.

### Команды и результаты

Перед тестами явно выполнено:

```text
docker compose up --build -d --wait --wait-timeout 120
docker compose up -d --wait --wait-timeout 120 db
```

`db` healthy, `web` запущен; HTTP `GET /` на `127.0.0.1:8000` возвращает
200 и заголовок `<h1>KazanCourts</h1>`.

```text
python manage.py check
System check identified no issues (0 silenced).

python -m pip check
No broken requirements found.

python manage.py makemigrations --check --dry-run
No changes detected

python manage.py migrate --check
exit code 0
```

В development-соединении через Django подтверждено:

```text
SELECT current_database(), current_user;
('kazan_courts_dev', 'kazan_dev')
```

После успешного первого прогона тестов выполнены `migrate --plan`,
`sqlmigrate bookings 0002`, `migrate --noinput` под `kazan_dev` в Compose.
Применены миграции contenttypes, auth и две миграции bookings.
Расширение `btree_gist` версии 1.8 установлено без повышения прав роли.
Фактическое ограничение из `pg_constraint`:

```sql
EXCLUDE USING gist (
    court_id WITH =,
    tstzrange(starts_at, ends_at, '[)'::text) WITH &&
) WHERE (status = 'active')
```

`seed_demo_data` в первый раз сообщил `7 courts and 3 customers created`,
при повторе — `0 courts and 0 customers created`.
В development-базе подтверждены семь кортов (три indoor, четыре outdoor),
три вымышленных клиента, ноль пользователей, броней и событий.

### Изолированные PostgreSQL-тесты

Тестовый пароль передан только через временную переменную окружения `DB_PASSWORD`.
Запуск одноразового контейнера:

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test test_database bookings --verbosity 2 --noinput
```

Итог: **24 теста, OK**. Django создал `test_kazan_courts`, применил все миграции,
выполнил тесты и удалил базу. Тест `test_database.py` подтвердил пользователя
`kazan_test` и имя `test_kazan_courts`.

Проверены:

- обязательный непустой телефон, допустимый общий телефон разных клиентов;
- FK и PROTECT для связанных объектов, JSON-снимки и порядок истории;
- допустимые типы/статусы и диапазоны координат;
- начало/конец, минимум 60 минут, обе десятиминутные границы включая микросекунды;
- одинаковые моменты в UTC и Europe/Moscow;
- запрет совпадений, частичных пересечений и вложенных интервалов одного корта;
- разрешение смежных интервалов и одинакового времени разных кортов;
- освобождение времени отменой и отказ конфликтующей реактивации;
- отказ конфликтующего UPDATE с сохранением прежнего времени;
- архив только отменённой брони;
- заполнение, повтор, сохранение изменённых записей и откат при неоднозначных именах.

Финальный повтор с проверкой состояния development-базы до/после:

```text
Ran 24 tests in 2.488s
OK
Destroying test database for alias 'default'...
```

SHA-256 снимков development-базы до и после совпал:
`252a20bb5b99ec67ac3df181ffcf778259d6453d0a60295ed6ffa696a1b27d2b`.
Снимок включал данные всех установленных моделей, владельца и ACL базы,
атрибуты development/test ролей и состояние последовательностей public.

Проверки каталогов после тестов:

```text
test_kazan_courts exists: false
kazan_courts_dev exists: true
has_database_privilege('kazan_test', 'kazan_courts_dev', 'CONNECT'): false
```

Реальная попытка TCP-подключения под `kazan_test` к development-базе завершилась
`permission denied for database`. Первоначальный проверочный скрипт ожидал
SQLSTATE ошибки соединения, который libpq не передал; проверка повторена по
сообщению PostgreSQL. Настройки приложения и права БД из-за этого не менялись.

### Проверка репозитория и границы доказательства

Проверены синтаксис Python, YAML CI и 17 PowerShell-блоков README.
В CI сохранены имя `django-check`, PostgreSQL 18.6, check, HTTP 200,
удаление тестовой базы, отсутствие SQLite, concurrency и pip cache.
Добавлены проверка соответствия миграций и запуск тестов bookings.

`git diff --check` проходит. Все Git-кандидаты проверены на совпадения
с локальными секретами, приватные ключи, GitHub-токены и посторонние локальные файлы:
совпадений нет. SQLite в образе приложения отсутствует.
Прежний контейнер `kazan-courts-postgres` и его mounts/ports/StartedAt
не изменились при проверках: контрольный отпечаток до/после совпал.

Конкурентные запросы через операцию создания брони остаются проверкой этапа 05.
Будущее время, месяц и сезон, операции с обязательным событием истории и запрет
физического удаления в действиях приложения ещё не реализованы.
PROTECT не запрещает прямой SQL владельцу БД; триггеры в этап 03 не входят.
Демонстрационные записи распознаются по именам: после их переименования команда
не гарантирует распознавание; параллельные seed-запуски не предусмотрены.
Новые CI-изменения окончательно проверяются в GitHub Actions после разрешённого push/PR.
