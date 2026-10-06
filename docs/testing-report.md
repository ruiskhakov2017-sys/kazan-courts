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

## Этап 04 — API чтения, вход и экран сотрудника, 6 октября 2026

Проверена текущая ветка `task/04-api-interface`, HEAD и база сравнения `main`:
`c00841de546d53094ec61d8d7a34500e5e43a945`. При повторной проверке код,
модели, миграции bookings, зависимости и Compose не редактировались.
Этот раздел заполнен по фактически выполненным локальным проверкам.
Commit, push и PR не выполнялись; GitHub Actions для этапа 04 — NOT_VERIFIED.

### Сборка, development-подключение и sessions

Повтор команды завершился успешно без изменения сетевых настроек или обхода
предыдущего Docker Hub TLS handshake timeout:

```text
docker compose up --build -d --wait --wait-timeout 120
Image kazan-courts-web Built
db healthy; web запущен на 127.0.0.1:8000
```

Образ содержит Python 3.14.8, Django 5.2.17 и psycopg 3.3.6.
Локальные секреты прочитаны из существующих контейнеров только в окружение
проверочных процессов; значения не выводились и не записывались в проект.

```text
python manage.py check
System check identified no issues (0 silenced).

python -m pip check
No broken requirements found.

SELECT current_database(), current_user;
('kazan_courts_dev', 'kazan_dev')

python manage.py migrate --plan
sessions.0001_initial — Create model Session

python manage.py migrate --noinput
Applying sessions.0001_initial... OK

python manage.py migrate --check
exit code 0

python manage.py makemigrations --check --dry-run
No changes detected
```

В `django_migrations` development-базы подтверждена запись `sessions.0001_initial`.
Учётную запись сотрудника в development-базе при этой проверке не создавали:
владелец проекта задаёт её пароль интерактивно командой `create_employee`
из README. В development-базе по-прежнему ноль пользователей.

### Полный прогон тестов на PostgreSQL

Перед прогоном явно выполнено `docker compose up -d --wait --wait-timeout 120 db`;
PostgreSQL healthy. В одноразовый контейнер передан тестовый пароль только
через временное окружение `DB_PASSWORD`:

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test test_database bookings --verbosity 2 --noinput

Found 56 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 56 tests in 38.967s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...
```

Все 24 прежних и 32 новых теста прошли. Применены стандартные миграции auth,
contenttypes, sessions и существующие миграции bookings в тестовой базе.
`test_database.py` подтвердил `test_kazan_courts` / `kazan_test`.

Новые проверки подтвердили:

- вход сотрудника через сессию, отказ при неверном пароле, неактивном пользователе
  и отсутствии допуска сотрудника; отклонение внешнего `next` URL;
- HTTP 200 рабочего экрана после входа, анонимный redirect, московскую дату,
  ссылки на CSS/JS и отсутствие кеширования защищённых ответов;
- CSRF для входа и выхода, POST-выход, завершение сессии и cookie HttpOnly/SameSite=Lax;
- чтение кортов и вымышленных контактов с явным набором полей;
- расписание по суткам Казани, переход через полночь и полуоткрытые границы;
- исключение отменённых/архивированных записей и записей другого корта;
- управляемые ответы 400/401/403/404/405/503 и пустое расписание с HTTP 200;
- неизменность Court, Customer, Booking и BookingEvent после запросов чтения;
- создание единственного несуперпользователя-сотрудника, хеширование пароля,
  отказ при повторном создании и при отсутствии скрытого ввода.

### Ручная браузерная проверка

На текущем development-сервере `GET /` перенаправил на `/login/?next=/`;
форма входа и её CSS отображаются корректно.

Для проверки рабочего экрана Django временно создал `test_kazan_courts` под
`kazan_test`. В отдельном одноразовом контейнере использовался тот же собранный
код и StaticFilesHandler, порт опубликован только как `127.0.0.1:8001`.
В эту тестовую базу загружены 7 синтетических кортов, 3 вымышленных клиента,
тестовый сотрудник и одна вымышленная бронь. Проверочный скрипт передан через
stdin и не добавлен в проект. Пароль fixture не является локальным credential.

В браузере фактически проверены:

- отправка формы входа и открытие рабочего экрана;
- загрузка CSS и JS с HTTP 200, 7 строк кортов и 3 строки клиентов;
- выбор корта и даты, обновление расписания и пустой результат другого корта;
- отображение `01.07.2026, 23:30` → `02.07.2026, 00:30` по Казани;
  эта бронь видна при выборе как 1, так и 2 июля;
- выход через POST с CSRF и возврат к форме входа;
- отсутствие ошибок/предупреждений JavaScript в захваченных browser logs.

Серверные HTTP-логи подтверждают 200 для рабочего экрана, CSS/JS и трёх API,
302 после входа/выхода и 401 для `/api/customers/` после выхода.
Браузер заблокировал отображение этого 401 как `net::ERR_BLOCKED_BY_CLIENT`;
формат JSON отказа подтверждён автоматическими тестами.
Запрос favicon вернул 404: значок не реализован, работу экрана это не нарушило.

SHA-256 снимка четырёх предметных моделей тестовой fixture до и после
браузерных действий совпал:
`f541740312b53005a8d8f37253aeab6369fa35b0a67b83b1d2e3e5abfae856ef`.
После завершения проверки Django удалил тестовую базу; одноразовый браузерный
контейнер также удалён. Development-аккаунты и предметные данные не менялись.

### Изоляция и неизменность development-данных

После полного тестового прогона и после браузерной проверки каталоги PostgreSQL
подтвердили:

```text
test_kazan_courts exists: false
kazan_courts_dev exists: true
has_database_privilege('kazan_test', 'kazan_courts_dev', 'CONNECT'): false
```

Повторная реальная TCP-попытка `kazan_test` → `kazan_courts_dev` закончилась
ожидаемым `permission denied for database`.
`kazan_dev`: без SUPERUSER/CREATEROLE/CREATEDB;
`kazan_test`: без SUPERUSER/CREATEROLE, с CREATEDB.

До и после всех проверок SHA-256 снимка development-базы совпал:
`1B4EA1E2C8B479C6C09321704D6177E525C949020A39E3B3641D03EA89333AFA`.
Снимок включал все поля Court, Customer, Booking, BookingEvent и состояние
последовательностей bookings. Сохранились 7 кортов, 3 клиента, 0 броней и 0 событий.
Служебная таблица sessions и запись о её миграции — согласованное изменение схемы,
они не включены в сравнение предметных данных.

Прежний `kazan-courts-postgres` остался остановленным; его ID, StartedAt,
mount `kazan-courts-postgres-data` и binding `127.0.0.1:5432` не изменились.

### Статические проверки, CI и содержимое diff

Проверены AST 28 Python-файлов, синтаксис JavaScript через `node --check`
и синтаксис 20 PowerShell-блоков README без исполнения инструкций.
YAML CI разобран существующим parser из runtime Playwright; зависимости
для проверок не устанавливались. Отдельно подтверждены прежние triggers main/PR,
имя `django-check`, PostgreSQL 18.6, concurrency и pip cache.

Текущие post-test assertions CI выполнены локально под `kazan_test` в служебной
`postgres`: тестовая база отсутствует, `/` возвращает redirect, `/login/` — HTTP 200
и ожидаемый заголовок. HTTP 200 защищённого экрана проверен внутри тестовой базы.
SQLite в собранном приложении отсутствует. GitHub Actions окончательно
проверяется после отдельно разрешённого push/PR.

Все 46 Git-кандидатов проверены на совпадения с фактическими локальными
секретами, приватные ключи, GitHub/AWS-токены, пароли в PostgreSQL URL и
посторонние credential/SQLite-файлы: совпадений нет. В тестах есть только явно
вымышленные fixture-пароли; CI bootstrap-пароль относится к временному CI-сервису.
`git diff --check` проходит; новые файлы отдельно проверены на ошибки пробелов.
Staging area пустая. По сравнению с началом повторной проверки изменён только
этот отчёт; код и остальные файлы сохранены.

Итог локальных проверок этапа 04: PASS. Отдельно остаются создание реального
учебного сотрудника владельцем проекта и будущий запуск GitHub Actions.
