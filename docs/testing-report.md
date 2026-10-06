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

## Этап 05 — создание брони, 6 октября 2026

Ветка `task/05-create-booking` создана от чистой синхронизированной main,
HEAD основания `13773d9291266ff72138dc12df4547b80655a071`.
Первый git fetch задержался и был прерван; обычный повтор с ограниченным
ожиданием завершился успешно. Сеть, TLS и глобальная конфигурация не менялись.
Commit, staging, push и PR не выполнялись.

### Реализованный объём

POST `/api/bookings/`: четыре поля JSON, явный timezone, сессия активного
сотрудника и штатный CSRF. Сервер проверяет существующие клиент/корт, телефон,
будущее время, обе десятиминутные границы, минимум час, календарный месяц и сезон.
Пользователь отдельно утвердил перенос отсутствующего дня на последний день
следующего месяца и допустимый конец открытой брони ровно 1 ноября 00:00.
Операция явно сохраняет Booking и BookingEvent(created) в одном atomic;
конфликт PostgreSQL распознаётся по SQLSTATE 23P01 и имени ограничения.

### Первая сборка и полный прогон — PASS

```text
docker compose up --build -d --wait --wait-timeout 120
Image kazan-courts-web Built
db healthy; web healthy; 127.0.0.1:8000

python manage.py check
System check identified no issues (0 silenced).

python -m pip check
No broken requirements found.

python manage.py migrate --check
exit code 0

python manage.py makemigrations --check --dry-run
No changes detected
```

Перед тестами выполнено `docker compose up -d --wait --wait-timeout 120 db`.
Тестовый пароль передан только в локальное окружение процесса DB_PASSWORD:

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test test_database bookings --verbosity 2 --noinput
Found 84 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 84 tests in 15.190s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...
```

Прошли 56 прежних и 28 новых тестов первоначальной реализации этапа 05.
Подтверждены правила времени/сезона, год/короткий февраль/високосный год,
сохранение снимка и автора, откат брони при ошибке истории, настоящий отказ
exclusion constraint после подставленного свободного precheck, API-коды,
авторизация, CSRF и видимость созданной брони в прежнем GET расписания.

Конкуренция проверена через TransactionTestCase и два HTTP-клиента с независимыми
соединениями к настоящей test_kazan_courts под kazan_test. Тестовый барьер
срабатывает после обеих реальных проверок занятости, до любого INSERT.
Ограниченное ожидание/SQL timeouts и закрытие worker-соединений находятся только
в тестах. Фактические результаты:

| Сценарий | PostgreSQL backend PID | Результат |
|---|---|---|
| Смежные интервалы | 10927, 10928 | 201/201; две брони, два события |
| Конфликт, повтор 1 | 10930, 10931 | 201/409; одна бронь, одно событие |
| Конфликт, повтор 2 | 10932, 10933 | 201/409; одна бронь, одно событие |
| Конфликт, повтор 3 | 10934, 10935 | 201/409; одна бронь, одно событие |

В каждом конфликтном повторе оба precheck вернули свободное время;
один запрос отклонён PostgreSQL. История проигравшего не сохранена.

### Последующие уточнения и предыдущая финальная сборка — BLOCKED

После успешного прогона уточнены три файла:

- forms.py: формат ISO offset требует минуты 00–59, исключая нормализацию
  ошибочных значений вроде +02:60;
- test_booking_rules.py: добавлены эти некорректные offsets в существующий тест;
- test_create_booking.py: запрос с настоящим JSON null проверяется явно.

Эти уточнения не вошли в образ первого успешного прогона. Попытка пересборки
окончательного исходного состояния остановилась до выполнения повторных тестов:

```text
#3 [internal] load metadata for docker.io/library/python:3.14.8-slim
failed to fetch anonymous token:
Get "https://auth.docker.io/token?scope=repository%3Alibrary%2Fpython%3Apull&service=registry.docker.io":
net/http: TLS handshake timeout
Dockerfile:1 — FROM python:3.14.8-slim
```

Обходы Docker Hub/TLS, смена образа или тестирование окончательного кода
через подмену файлов старого контейнера не применялись. На этой попытке повторный
полный прогон был остановлен, а web оставался на предыдущей проверенной сборке.
Блокировка закрыта успешной пересборкой и повтором ниже.

### Независимые итоговые проверки

Синтаксис окончательных исходников проверен стандартным Python AST без импорта
проекта и записи pyc: 33 файла — OK. Проверены 20 PowerShell-блоков README.
`git diff --check` и отдельная проверка новых файлов на ошибки пробелов прошли.

После тестов и неудачной пересборки PostgreSQL подтвердил:

```text
test_kazan_courts exists: false
kazan_courts_dev exists: true
has_database_privilege('kazan_test', 'kazan_courts_dev', 'CONNECT'): false
development counts: 7 courts, 3 customers, 0 bookings, 0 events
```

SHA-256 снимка всех полей предметных таблиц и последовательностей bookings
в development до и после совпал:
`1B4EA1E2C8B479C6C09321704D6177E525C949020A39E3B3641D03EA89333AFA`.
Старый kazan-courts-postgres, его mount, port binding, ID, status и StartedAt
не изменились. Проверено 51 Git-кандидат на реальные локальные секреты,
private keys, GitHub/AWS-токены, PostgreSQL URL с паролем и посторонние локальные
файлы: совпадений нет. Модели, миграции, settings, зависимости, Compose, Dockerfile,
CI и файлы интерфейса не менялись. CI автоматически обнаружит новые тесты,
но GitHub Actions для этих изменений ещё не запускался.

На момент предыдущей попытки окончательная версия ещё требовала успешной
пересборки, полного повтора тестов и проверки изоляции баз. Эти проверки
выполнены по отдельному запросу пользователя и описаны далее.
Перенос/отмена/архив, погода и состояние покрытия, полный интерфейс создания
относятся к следующим этапам. Динамические правила и обязательная история
обеспечиваются через create_booking; прямой ORM/SQL этот сервис обходит.

### Финальная проверка окончательной версии — PASS

По запросу пользователя 6 октября 2026 повторена обычная сборка без изменения
кода, образа Python или сетевых/TLS-настроек. Сборка завершилась успешно:

```text
docker compose up --build -d --wait --wait-timeout 120
Image kazan-courts-web Built
db healthy; web healthy
```

SHA-256 всех 33 Python-файлов внутри собранного web совпали с локальными
исходниками, включая последние уточнения ISO offsets и тест JSON null.
Прошли manage.py check, pip check, migrate --check и
makemigrations --check --dry-run; новых миграций нет, development-миграции
в этом повторе не применялись.

Перед каждым тестовым прогоном явно выполнено
`docker compose up -d --wait --wait-timeout 120 db`; db healthy,
test_kazan_courts отсутствует. Credentials переданы только через окружение
процесса, без записи в файлы или вывода значений.

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test test_database bookings --verbosity 2 --noinput
Found 84 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 84 tests in 71.790s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...

docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test bookings.tests.test_booking_concurrency --verbosity 2 --noinput
Found 2 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 2 tests in 1.327s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...
```

Полный набор включает 56 прежних и 28 новых тестов. Конкурентные сценарии
прошли в полном наборе и отдельно на той же окончательной сборке:

| Сценарий | PID в полном наборе | PID отдельного повтора | Результат каждого прогона |
|---|---|---|---|
| Смежные интервалы | 12948, 12949 | 13353, 13354 | 201/201; две брони, два события |
| Конфликт, повтор 1 | 12951, 12952 | 13356, 13357 | 201/409; одна бронь, одно событие |
| Конфликт, повтор 2 | 12953, 12954 | 13358, 13359 | 201/409; одна бронь, одно событие |
| Конфликт, повтор 3 | 12955, 12956 | 13360, 13361 | 201/409; одна бронь, одно событие |

В каждом конкурентном запросе подтверждены test_kazan_courts / kazan_test
и независимый backend PID. Оба настоящих precheck завершались свободным
результатом до INSERT; конфликт отклоняла PostgreSQL, история проигравшего
не сохранялась.

После отдельного повтора PostgreSQL подтвердил отсутствие test_kazan_courts,
наличие kazan_courts_dev и запрет CONNECT для kazan_test к development.
В development осталось 7 кортов, 3 клиента, 0 броней и 0 событий; SHA-256
полного предметного снимка и последовательностей до/после совпал с
`1B4EA1E2C8B479C6C09321704D6177E525C949020A39E3B3641D03EA89333AFA`.
Отдельный kazan-courts-postgres и его mount/port/ID/status/StartedAt не изменились.

Все 51 Git-кандидат совпали с файлами до повтора проверки. Секреты и посторонние
локальные файлы не найдены; git diff --check и отдельная проверка новых файлов
прошли. Staging оставался пустым, HEAD/main/origin/main не изменились.
Дополнительная проверка секретов сначала не запустилась из-за лимита
автоматической проверки разрешений; после команды пользователя «продолжи»
обычный повтор прошёл. Обход проверки разрешений не применялся.

После прогона по отдельному запросу пользователя обновлены только этот отчёт
и описание фактической transaction boundary в docs/design.md. Код сохранён.
Итог локальных проверок окончательной версии этапа 05: **PASS**.
Изменения представлены для code review; staging, commit, push и PR
не выполнялись. GitHub Actions для этих изменений ещё не запускался.
