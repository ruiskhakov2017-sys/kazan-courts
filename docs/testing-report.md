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

## Этап 06 — перенос, отмена, архив и история

### Локальная проверка 6 октября 2026 — FAIL

Рабочая ветка task/06-booking-actions создана от чистой синхронизированной
main, commit 9d2a4c2f9ccedcd27c1da2fd723bff77a26e878e. Реализованы серверные
действия, чтение рабочего списка и истории; интерфейс сотрудника не изменён.

Обычная сборка и запуск прошли без обхода Docker Hub или изменения TLS:

```text
docker compose up --build -d --wait --wait-timeout 120
Image kazan-courts-web Built
db healthy; web healthy
```

Прошли manage.py check, pip check, migrate --check и
makemigrations --check --dry-run. Миграции development не применялись;
новых миграций нет. AST parse всех 35 Python-файлов прошёл, SHA-256
этих файлов внутри собранного web совпали с локальными исходниками.

Перед тестами явно выполнено docker compose up -d --wait --wait-timeout 120 db;
db healthy, test_kazan_courts отсутствовала. Тестовые credentials переданы
только через окружение процесса, без записи в файлы или вывода значений.

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test test_database bookings --verbosity 2 --noinput
Found 106 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 106 tests in 96.068s
FAILED (failures=1)
Destroying test database for alias 'default' ('test_kazan_courts')...
```

Все 18 новых тестов действий/API и 4 новых конкурентных теста прошли.
Проверены повторные действия, границы времени, телефон, собственный интервал,
откат при ошибке события, права сотрудника, CSRF, ошибки JSON, запрет DELETE,
неизменность старых событий, архив прошлой отменённой записи, чтение истории
после архива и отсутствие предметных изменений после чтения.

| Новый конкурентный сценарий | Backend PID | Фактический результат |
|---|---|---|
| Перенос/отмена одной брони, повтор 1 | 21342, 21343 | 409/200; согласованная история |
| Перенос/отмена одной брони, повтор 2 | 21344, 21345 | 409/200; согласованная история |
| Перенос/отмена одной брони, повтор 3 | 21351, 21352 | 200/200; последовательные изменения и история |
| Создание/перенос на один интервал, повтор 1 | 21355, 21356 | 409/201; один победитель и одно новое событие |
| Создание/перенос на один интервал, повтор 2 | 21357, 21358 | 200/409; один победитель и одно новое событие |
| Создание/перенос на один интервал, повтор 3 | 21359, 21360 | 409/201; один победитель и одно новое событие |
| Две отмены | 21362, 21363 | 200/409; одно событие отмены |
| Два архива | 21364, 21365 | 200/409; одно событие архива |
| Проверка now после реального ожидания row lock | 21373 | 409 booking_already_started; бронь и история сохранены |

Каждое новое конкурентное соединение подтвердило test_kazan_courts / kazan_test.
В гонке создания/переноса оба настоящих precheck сначала видели свободное время.

**Найденная проблема.** В неизменённом прежнем
test_two_simultaneous_requests_leave_one_booking_and_event_repeatedly
на первом повторе PostgreSQL обнаружил deadlock при проверке exclusion constraint:
SQLSTATE 40P01, backend PID 21379 и 21380. Получены HTTP 201/503 вместо 201/409.
Текущий сервис преобразует только 23P01 / booking_active_no_overlap в конфликт;
OperationalError 40P01 попадает в общий ответ 503. Аналогичная обработка без
повтора 40P01 уже есть в main. Следующие два повтора и прежний тест смежных
интервалов прошли, но полный прогон не считается успешным.

После завершения PostgreSQL подтвердил:

```text
test_kazan_courts exists: false
kazan_courts_dev exists: true
has_database_privilege('kazan_test', 'kazan_courts_dev', 'CONNECT'): false
development counts: 7 courts, 3 customers, 0 bookings, 0 events
```

SHA-256 всех предметных полей и последовательностей bookings до/после совпал:
1B4EA1E2C8B479C6C09321704D6177E525C949020A39E3B3641D03EA89333AFA.
Старый kazan-courts-postgres, его volume/mount, port, ID, status и StartedAt
не изменились. 42 файла вне scope этапа 06 совпали с исходными хешами.
Проверка 53 Git-кандидатов не нашла локальных credentials, private keys,
GitHub/AWS-токенов, PostgreSQL URL с паролем или посторонних локальных файлов.
git diff --check и проверка пробелов новых файлов прошли.

Отдельный повтор конкурентных тестов не выполнен после неуспешного полного
прогона. Обработка deadlock требует согласования изменения плана; код после
ошибки не исправлялся, тесты не перезапускались для получения случайного PASS.
Staging, commit, push и PR не выполнены; CI для этапа 06 не запускался.
Готовность к commit: **нет**, до исправления и успешного полного повторного
прогона с отдельной проверкой конкуренции.

### Финальная проверка после согласованного исправления — PASS, 7 октября 2026

Пользователь согласовал один повтор всей серверной операции только при SQLSTATE
40P01. `_retry_deadlock` перехватывает OperationalError после выхода из atomic
и отката; сотрудник, данные, блокировка и время проверяются заново. Максимум
две попытки. Повторный deadlock и другие ошибки БД остаются 503; 23P01 для
booking_active_no_overlap остаётся 409 без повтора. В конкурентных тестах
барьер применяется к первым попыткам,
проверки ответов и целостности не ослаблены.

Добавлены четыре детерминированных теста. После настоящей записи в тестовую
PostgreSQL принудительно выбрасывается OperationalError с причиной 40P01:
проверены откат Booking и BookingEvent до новой попытки для всех четырёх
операций, повторная проверка времени, максимум две попытки и отсутствие
повтора для 55P03, 40001 или OperationalError без SQLSTATE. Эти искусственные
ошибки проверяют обработку независимо от случайного появления реального deadlock.
Сами конкурентные запросы используют настоящие независимые PostgreSQL-соединения.

Окончательная версия успешно собрана обычным docker compose up --build -d
--wait --wait-timeout 120; db и web healthy. Обходов Docker Hub или изменений
TLS не было. Прошли manage.py check, pip check, migrate --check,
makemigrations --check --dry-run; новых миграций нет, development-миграции
не применялись. Все 35 Python-файлов прошли AST parse; их SHA-256 внутри
собранного web совпали с локальными исходниками.

Перед каждым прогоном явно запущена db с ожиданием healthy, проверено отсутствие
test_kazan_courts. После полного набора отсутствие базы также проверено перед
отдельным конкурентным прогоном. Credentials использованы только в окружении.

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test test_database bookings --verbosity 2 --noinput
Found 110 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 110 tests in 23.517s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...

docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test bookings.tests.test_booking_concurrency bookings.tests.test_booking_actions_concurrency --verbosity 2 --noinput
Found 6 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 6 tests in 3.004s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...
```

Полный набор включает 84 прежних и 26 новых тестов этапа 06. Все прошли.

| Конкурентный сценарий | PID полного набора | PID отдельного повтора | Результат |
|---|---|---|---|
| Смежные создания | 91963/91964 | 92109/92110 | 201/201, две брони и события |
| Конфликт создания, три повтора | 91966/91967; 91968/91969; 91970/91971 | 92112/92113; 92114/92115; 92116/92117 | 201/409, одна бронь и событие в каждом повторе |
| Перенос/отмена одной брони, три повтора | 91942/91943; 91944/91945; 91946/91947 | 92119/92120; 92121/92122; 92129/92130 | Согласованная последовательная история; отмена 200, перенос 200 или 409 по порядку действий |
| Создание/перенос, три повтора | 91949/91950; 91951/91952; 91953/91954 | 92133/92134; 92135/92136; 92137/92138 | Один успех 200 или 201, один 409; одно новое событие |
| Две отмены | 91956/91957 | 92140/92141 | 200/409, одно событие отмены |
| Два архива | 91958/91959 | 92142/92143 | 200/409, одно событие архива |
| Проверка now после настоящего ожидания row lock | 91961 | 92145 | 409 booking_already_started, данные и история не изменены |

Каждое конкурентное соединение подтвердило test_kazan_courts / kazan_test.
В конфликтующих первых попытках оба реальных precheck видели свободное время.
Дополнительные попытки не проходят тестовый барьер и выполняют новый real precheck.

После отдельного прогона PostgreSQL подтвердил отсутствие test_kazan_courts,
наличие kazan_courts_dev и запрет CONNECT для kazan_test к development.
В development осталось 7 кортов, 3 клиента, 0 броней и 0 событий. SHA-256
всех предметных полей и последовательностей до/после совпал:
1B4EA1E2C8B479C6C09321704D6177E525C949020A39E3B3641D03EA89333AFA.
Старый kazan-courts-postgres и его volume/mount, port, ID, status, StartedAt
не изменились. Через Django подтверждено kazan_courts_dev / kazan_dev;
анонимный GET / возвращает redirect на login, GET /login/ — 200 с заголовком
KazanCourts. SQLite отсутствует.

Scope включает 12 файлов: к исходным 11 добавилась только адаптация барьера
в прежнем test_booking_concurrency.py. Остальные 41 файл совпали с исходными
хешами; модели, миграции, settings, зависимости, Compose, Dockerfile, CI
и интерфейс не изменены. Проверка 53 Git-кандидатов на реальные локальные
credentials, private keys, GitHub/AWS-токены, PostgreSQL URL с паролем и
посторонние файлы прошла; git diff --check и проверка новых файлов прошли.

Итог локальной окончательной проверки: **PASS**, набор готов к commit.
На момент этой проверки до commit GitHub Actions этапа 06 ещё не запускался.
Публикация и CI разрешены пользователем; merge пока не разрешён.

## Этап 07 — погода и готовность открытого покрытия, 7 октября 2026

Рабочая ветка: `task/07-weather`, исходная чистая `main`:
`5080779d24c91d2c8c53e4c9c8403f13ca920858`. Результаты этого подраздела
относятся к исходной реализации, позднее зафиксированной commit
`0ec9efefdea492aadc62d806fff4171633ae5afa`. GitHub Actions для этого commit
завершился SUCCESS. Последующее уточнение независимости покрытия и погоды
и его локальные проверки описаны отдельно ниже.

### Окончательная версия и окружение

Python 3.14.8, Django 5.2.17, psycopg 3.3.6, PostgreSQL 18.6.
Использован существующий Compose с отдельным volume
`kazan-courts_compose_postgres_data`. Старый контейнер
`kazan-courts-postgres` и его volume не использовались для тестов.
Credentials передавались только через локальное окружение процесса.

Сборка окончательной версии обычной командой
`docker compose up --build -d --wait --wait-timeout 120` успешно завершена
перед финальным повтором. Docker Hub/TLS не обходились. Во время финального
повтора код не изменялся. SHA-256 всех 46 Python/template/CSS/JS-файлов внутри
собранного `web` совпали с локальными исходниками перед тестами.

Предварительный полный прогон: 145 тестов, 47.164 с, OK; отдельный
конкурентный прогон: 11 тестов, 7.351 с, OK. После него перед окончательной
сборкой была уточнена обработка числового WMO-кода `61.0`: он распознаётся
как целый код `61`, а дробный `61.5`, строки и bool остаются unknown.
Соответствующий тест обновлён. Приведённые ниже финальные результаты получены
уже после этой правки и повторной сборки.

### Финальный полный прогон — PASS

Прошли `manage.py check` (0 issues), `python -m pip check`
(No broken requirements found), `makemigrations --check --dry-run`
(No changes detected), `migrate --check` (exit 0) и
`node --check static/bookings/employee.js` (exit 0).
Миграция `bookings.0003_booking_weather` ранее применена к учебной
development-базе; при финальном повторе development-миграции не запускались.

Перед каждым прогоном явно выполнено:

```text
docker compose up -d --wait --wait-timeout 60 db
db: healthy
test_kazan_courts exists: false
```

Финальный полный набор:

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD -e WEATHER_NETWORK_ENABLED=0 web python manage.py test test_database bookings --verbosity 2 --noinput
Found 145 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 145 tests in 24.532s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...
exit 0
```

Набор включает 110 прежних и 35 новых тестов. Проверены:

- точный набор дождя/мороси/ливня: 51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
  80, 81, 82; четыре шестичасовых периода и полуоткрытые границы;
- локальное время Казани, переход через полночь, частичное пересечение периода,
  приоритет известного дождя над неполными данными;
- unknown для отсутствующих/неподдерживаемых данных и дат за горизонтом,
  видимое предупреждение, сохранённый флаг перепроверки и история;
- контракт клиента Open-Meteo с подменённым transport, timeout, ограничение
  размера ответа, повреждённый JSON, массивы и timestamps; целый код `61.0`;
- отсутствие transport-вызова при отключённой сети и для закрытых кортов;
- запрет дождевых периодов и недоступного покрытия при создании/переносе,
  сохранность исходной брони при отказе;
- ручная перепроверка будущих действующих броней, контакты затронутых клиентов,
  отсутствие автоматической отмены/переноса, отдельные события истории;
- откат всей пачки при ошибке истории и повтор deadlock с одним ранее
  полученным снимком прогноза;
- закрытие покрытия, подтверждение готовности и отметка осмотра; в исходной
  версии проверялся запрет подтверждения при текущем дожде (отменён уточнением
  ниже), а также сохранение запрета будущего мокрого периода;
- авторизация сотрудника, CSRF, HTTP-методы, строгий JSON, отсутствие
  предметных записей после read-only API и неуспешных действий;
- миграция старых indoor/outdoor-броней без переписывания прежних JSON-событий,
  PostgreSQL constraint для weather_status;
- прежние правила времени, сезон, календарный месяц, ограничения PostgreSQL,
  авторизация, API, отмена/архив, последовательность истории и конкуренция.

### Отдельный повтор конкурентных тестов — PASS

После полного набора PostgreSQL отдельно подтвердил отсутствие
`test_kazan_courts`. Перед повтором снова явно проверена healthy `db`.

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD -e WEATHER_NETWORK_ENABLED=0 web python manage.py test bookings.tests.test_booking_concurrency bookings.tests.test_booking_actions_concurrency bookings.tests.test_court_weather_concurrency --verbosity 2 --noinput
Found 11 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 11 tests in 4.550s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...
exit 0
```

Каждое проверяемое конкурентное соединение подтвердило
`test_kazan_courts / kazan_test`. Ожидание блокировки подтверждалось реальным
`pg_stat_activity.wait_event_type = Lock`, а не только тестовым барьером.

| Новый сценарий Court | PID полного набора | PID отдельного повтора | Результат |
|---|---|---|---|
| Закрытие раньше создания | 2649 | 2794 | Реальный Lock wait; 409, без брони/события |
| Создание раньше закрытия | 2651/2652 | 2796/2797 | Запись покрытия ждёт; 201/200, бронь сохранена |
| Время создания после ожидания Court | 2654 | 2799 | 400, без записей |
| Время переноса после ожидания Court | 2656 | 2801 | 409, исходная бронь/история сохранены |
| Два одновременных `FOR SHARE` | 2658/2659 | 2803/2804 | 201/201, две брони/события; совместимость чтений |

Шесть прежних конкурентных тестов тоже прошли в обоих финальных прогонах:
смежные создания, три повтора конфликта создания, три повтора переноса/отмены
одной брони, три повтора создания/переноса, двойные отмена/архив и проверка
времени после ожидания Booking lock. Конфликт оставляет одного победителя
и одно новое событие; повторные действия не дублируют историю.

### Браузерная проверка интерфейса — PASS, синтетический прогноз

Ранее в этой же реализации выполнена ручная браузерная проверка на отдельном
временном контейнере `kazan-courts-weather-qa`, только на `test_kazan_courts`
под `kazan_test`. Использованы вымышленные корт, клиент, телефон, сотрудник
и прогноз. Реальная development-учётная запись не использовалась.
Интерфейс после этой проверки не изменялся; последующая правка `61.0`
касалась только parser и его теста.

Подтверждены вход/выход, CSS/JS 200, четыре периода, недоступность погодных
действий для indoor, ручная перепроверка и показ затронутой брони/контакта,
закрытие до drying и подтверждение до available. Будущий дождевой период
после подтверждения оставался blocked. За пределами синтетического
16-дневного прогноза показаны четыре unknown-периода и предупреждение.
Ошибок JavaScript в консоли нет. Бронь при перепроверке/закрытии не удалялась.

Скриншот сохранён вне репозитория:
`C:/Users/ruisk/.codex/visualizations/2026/09/29/01a0ed28-6d1b-77d0-b611-f18177682002/kazan-courts-stage07-weather.jpg`.
Временный QA-сервер завершён штатно, тестовая база удалена runner,
QA-контейнер удалён. Его отсутствие повторно проверено после финальных тестов.

### Изоляция и сохранность данных — PASS

После финального полного набора и отдельного повтора подтверждено:

```text
test_kazan_courts exists: false
kazan_courts_dev exists: true
has_database_privilege('kazan_test', 'kazan_courts_dev', 'CONNECT'): false
development: 7 courts, 3 customers, 0 bookings, 0 events
bookings.0003_booking_weather applied: true
```

SHA-256 всех предметных полей Court/Customer/Booking/BookingEvent и
последовательностей bookings совпал с исходным снимком до этапа 07:
`1B4EA1E2C8B479C6C09321704D6177E525C949020A39E3B3641D03EA89333AFA`.
Старый отдельный PostgreSQL-контейнер сохранил ID, status, StartedAt,
port и volume/mount. Development-данные и его volume не изменились.

Все 43 Python-файла окончательного образа прошли AST parse. Через Django
повторно подтверждено `kazan_courts_dev / kazan_dev`,
`WEATHER_NETWORK_ENABLED = false`, анонимный GET `/` — 302 на login,
GET `/login/` — 200; SQLite-файлов нет.
Вспомогательный запуск `python -` сначала завершился ImproperlyConfigured,
так как в проверочной команде не был указан DJANGO_SETTINGS_MODULE.
После явного задания `config.settings` команда прошла; файлы проекта
для этого не исправлялись.

### Scope, секреты и оставшиеся границы проверки

Проверка 61 Git-кандидата на значения четырёх реальных локальных Compose
credentials, private keys, GitHub/AWS-токены, PostgreSQL URL с паролем и
посторонние локальные файлы прошла. Секреты не выведены и не записаны в Git.
`git diff --check` и отдельная проверка пробелов восьми новых файлов прошли.
38 файлов `main` вне scope совпали с исходными SHA-256; зависимости,
Dockerfile, Compose и CI не изменялись.

На момент исходных локальных тестов реальный Forecast API Open-Meteo:
**NOT_VERIFIED** — отдельного разрешения на запрос ещё не было.
В тестовых запусках явно передано `WEATHER_NETWORK_ENABLED=0`;
тесты включённого клиента используют подменённый transport. Контракт и
таблица кодов проверены по документации и синтетическим данным, это не
доказательство реального ответа сервиса.

Commit выполнен: `0ec9efefdea492aadc62d806fff4171633ae5afa`,
`feat: add weather checks and surface readiness` (23 файла). Push в
`origin/task/07-weather` выполнен. PR №8 открыт:
https://github.com/ruiskhakov2017-sys/kazan-courts/pull/8.
GitHub Actions `django-check`: **SUCCESS**, run `37601265797`:
https://github.com/ruiskhakov2017-sys/kazan-courts/actions/runs/37601265797.
Статус job и всех шагов подтверждён метаданными GitHub. Последующая попытка
прочитать полный лог остановилась на `net/http: TLS handshake timeout`;
обходов или повторных запросов для получения лога не выполнялось.
Merge **не выполнен**. На момент первого commit реальный Open-Meteo
оставался **NOT_VERIFIED**; позднейшая live-проверка описана ниже.
Этот успешный CI относится к commit `0ec9efe`. Публикация уточнения и её CI
описаны ниже. Итог исходных локальных финальных тестов: **PASS**.

### Уточнение перед merge — финальный локальный прогон, 7 октября 2026

По решению пользователя физическая готовность покрытия и погодный запрет
разделены. Убран только отказ подтверждения drying → available при текущем
blocked; отметка осмотра и погодное предупреждение сохраняются. Погодная
проверка создания/переноса и ограничения maintenance не изменены.
README, requirements, design и decision-log приведены к этому решению.

Окончательная версия уточнения успешно собрана обычной командой
`docker compose up --build -d --wait --wait-timeout 120`; db и web healthy.
Docker Hub/TLS не обходились. Прошли manage.py check, pip check,
makemigrations --check --dry-run и migrate --check. Новых миграций нет,
development-миграции не применялись. AST всех 43 Python-файлов прошёл;
SHA-256 46 исходников/template/CSS/JS внутри образа совпали с локальными.

Перед прогоном явно проверена healthy db и отсутствие test_kazan_courts.

```text
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD -e WEATHER_NETWORK_ENABLED=0 web python manage.py test test_database bookings --verbosity 2 --noinput
Found 145 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
Ran 145 tests in 45.377s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...
exit 0
```

Обновлённый регрессионный тест подтвердил HTTP 200 при подтверждении покрытия
с текущим blocked, available и время осмотра в Court, погодное предупреждение,
неизменность текущих/будущих блоков и существующих брони/истории. Создание
и перенос в текущий и будущий blocked возвращают 409 weather_conflict
без изменения предметных данных. Создание в clear после осмотра успешно.
Проверка рядом с UTC-полуночью подтвердила успешный осмотр и предупреждение
для текущего блока именно по дате Казани. Unknown, maintenance, авторизация,
CSRF и прежние сценарии также прошли.

Полный набор включает все 11 конкурентных тестов PostgreSQL; они прошли.
Браузерный прогон для этого уточнения не повторялся: templates/CSS/JS
не изменены, новый ответ API проверен через Django Client.

После прогона test_kazan_courts отсутствует, kazan_courts_dev существует,
CONNECT для kazan_test к development по-прежнему false. В development
7 кортов, 3 клиента, 0 броней и 0 событий. Все предметные поля и
последовательности сохранили SHA-256 до/после:
`1B4EA1E2C8B479C6C09321704D6177E525C949020A39E3B3641D03EA89333AFA`.
Повторное соединение Django: kazan_courts_dev / kazan_dev;
WEATHER_NETWORK_ENABLED = false. На момент этого локального прогона
реальный Open-Meteo: **NOT_VERIFIED**, сетевые вызовы не выполнялись.

Scope уточнения: 7 изменённых файлов, без новых файлов; 54 остальных файла
совпали с исходными хешами. Проверка 61 Git-кандидата на реальные локальные
credentials, private keys и токены прошла; git diff --check прошёл.
На момент локальной проверки HEAD и origin/task/07-weather были на 0ec9efe,
staging пустой; уточнение ещё не было закоммичено или отправлено.
Итог локального уточнения: **PASS**.

После отдельного разрешения выполнен commit
`97236ea8c986294cad2ec35b7de7161215b29588`,
`fix: separate surface readiness from weather bans` (7 файлов), и push
в `origin/task/07-weather`. PR №8 обновлён; `django-check` для этого commit:
**SUCCESS**, run `37610190111`:
https://github.com/ruiskhakov2017-sys/kazan-courts/actions/runs/37610190111.
Job и все шаги успешно завершены. Финальное повторное чтение PR через GraphQL
завершилось TLS handshake timeout; повторов не было. При следующей read-only
проверке перед публикацией отчёта реальной проверки подтверждены OPEN PR №8, HEAD 97236ea,
основная ветка main и SUCCESS этого CI. Merge не выполнен.

### Один реальный read-only вызов Open-Meteo — PASS

Пользователь отдельно разрешил ровно один реальный вызов. Проверка выполнена
7 октября 2026 в 14:06:08 МСК на коде commit `97236ea` через настоящий
`bookings.weather_client.fetch_forecast()` существующего Compose web.
Погодный компонент внутри контейнера совпал с локальными исходниками.

`WEATHER_NETWORK_ENABLED=1` передано только отдельному Python-процессу
через docker exec. Стандартная проверка TLS сохранена; timeout 5 секунд.
Перенаправления отключены в проверочном процессе, чтобы не сделать второй
запрос. Transport-инструментирование вызвало настоящий urlopen и наблюдало
ответ; прогноз и тело ответа не подменялись. Повторов не было.

```text
GET https://api.open-meteo.com/v1/forecast?latitude=55.793000&longitude=49.123000&hourly=weather_code&timezone=Europe%2FMoscow&forecast_days=16
HTTP 200; Content-Type: application/json; response bytes: 8367
request_count: 1; database_query_count: 0
timezone: Europe/Moscow; utc_offset_seconds: 10800
hourly fields: time, weather_code
time count: 384; weather_code count: 384; parsed hours: 384
first hour: 2026-10-07T00:00:00+03:00
last hour: 2026-10-22T23:00:00+03:00
received codes: 0, 1, 2, 3, 61, 80
invalid or missing codes: 0; unavailable_reason: empty
integration_success: true; process exit code: 0
```

На том же полученном Forecast чистая политика рассчитала четыре блока
на 7 октября по Казани:

| Период | Статус | Дождевые коды |
|---|---|---|
| 00:00–06:00 | blocked | 61 |
| 06:00–12:00 | clear | — |
| 12:00–18:00 | clear | — |
| 18:00–24:00 | clear | — |

SQL-выполнение было запрещено проверочным guard; ни запросов к БД, ни
открытых Django database connections не было. После проверки обычный
процесс контейнера подтвердил WEATHER_NETWORK_ENABLED = false.
Сеть не включалась в Compose или CI. HEAD остался 97236ea, staging пустой,
рабочее дерево чистое; SHA-256 всех 61 Git-файла до/после совпали.

Статус реального Open-Meteo: **VERIFIED / PASS для этого единственного
ответа** — HTTPS, JSON-контракт, parser и четыре блока проверены вместе.
Остальные коды и failure-сценарии по-прежнему проверяются синтетическими
тестами. Сетевой вызов при подготовке и публикации этого отчёта не повторяется.

## Этап 08 — полный рабочий сценарий сотрудника

Проверка выполнена 7 октября 2026 в `task/08-employee-workflow` от чистой
main `fcacdeca73f7aba6e474d0fe321bd56c5fa912dc`.

Изменены ровно шесть согласованных файлов: templates/home.html,
static/bookings/employee.js, static/bookings/employee.css,
config/views.py (только описание модуля), README и этот отчёт.
Модели, миграции, API, сервисы, settings, зависимости и CI не изменены.
Карта, тарифы, письма и форма архивирования не добавлялись.

### Сборка и окончательный полный прогон — PASS

Обычный `docker compose up --build -d --wait --wait-timeout 120` прошёл;
db healthy, web запущен. Docker Hub/TLS не обходились. SHA-256 всех
47 Python-файлов, шаблонов, CSS и JS внутри окончательного образа совпали
с локальными. Прошли manage.py check, pip check,
makemigrations --check --dry-run, migrate --check, Node --check employee.js
и AST всех 43 Python-файлов. Миграции development не запускались.
SQLite не создана.

Предварительный прогон: 145 тестов, 26.330s, OK. После локального review
уточнены состояние загрузки расписания и очистка устаревшего списка
погодных предупреждений. Образ пересобран, браузер проверен на нём.
Прерванная пользователем попытка финального прогона не считается PASS.
После команды завершить работу получен окончательный результат:

```text
docker compose up -d --wait --wait-timeout 120 db
FINAL_TEST_PRECONDITIONS: db healthy; test database absent
docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD -e WEATHER_NETWORK_ENABLED=0 web python manage.py test test_database bookings --verbosity 2 --noinput
Found 145 test(s).
Creating test database for alias 'default' ('test_kazan_courts')...
System check identified no issues (0 silenced).
Ran 145 tests in 23.260s
OK
Destroying test database for alias 'default' ('test_kazan_courts')...
exit 0
```

Все 11 конкурентных PostgreSQL-тестов входят в этот прогон и прошли:
конфликтующее создание, создание/перенос, изменение одной брони,
соседние интервалы и реальные ожидания блокировок Court/Booking.
test_database.py подтвердил test_kazan_courts / kazan_test.

### Браузер и отказные сценарии — PASS

DiscoverRunner создал/мигрировал отдельную test_kazan_courts под kazan_test
для одноразового QA-сервера на окончательном образе. Использованы только
вымышленные корты, клиенты и сотрудник. Порт опубликован на 127.0.0.1:8001;
отдельное имя localhost изолировало cookies от development.
Подставленные прогнозы, задержки, контролируемые сбои и счётчики запросов
существовали только в памяти QA-процесса; QA-код/credentials в Git отсутствуют.

| Сценарий | Фактический результат |
|---|---|
| Вход, CSS/JS | Экран загружен, 7 кортов и 3 клиента; browser error logs пусты. |
| Создание → перенос → отмена | Бронь №2: 8 октября 13:20–14:20 → 15:20–16:20 → cancelled; в PostgreSQL три события с сотрудником и причинами. |
| Конфликтующее создание/перенос | 409; ввод сохранён, прежняя бронь/история не изменены. |
| Интервал менее часа | 18:00–18:30 отклонён; ошибка рядом с полем конца, без записи. |
| Подтверждение отмены | Без флажка POST не отправлен. После подтверждения один POST; запись и история сохранены. |
| Отменённая запись и занятость | По умолчанию скрыта, флажок показывает снова; свободные промежутки не считают её занятой. |
| Безопасный вывод и старая история | Причина с HTML показана буквально, элементов b в истории нет; событие без погодных полей показано без ошибки. |
| Unknown | Открытая бронь №3 сохранена с предупреждением, unknown и событием. |
| Ручная перепроверка | Синтетический дождь отметил №3 blocked; бронь сохранена, телефон показан, кнопка открыла её корт/дату/карточку. |
| Покрытие отдельно от погоды | available → drying → available после осмотра при дождевом блоке; погодный запрет сохранился. Перенос в 16:00–17:00 отклонён с сохранением ввода. |
| Clear | На том же корте создана №4 в 18:00–19:00 с clear. Устаревшие погодные предупреждения очищены с объяснением, автоматической перепроверки нет. |
| Двойной щелчок | При задержанном POST изменение и смена контекста отключены; один запрос, одна №7 и одно событие. |
| Ошибка GET после успешного POST | №5 сохранена; подтверждение записи и ошибка обновления показаны отдельно. Ручное обновление вернуло запись. |
| Нечитаемое подтверждение POST | №6 реально сохранена; интерфейс сообщил неизвестный результат, POST не повторён; ручное обновление показало запись. |
| CSRF 403 / server 503 | HTML-отказ показан понятным текстом, 503 — как неизвестный результат; ложного успеха/записи/повтора нет. |
| Сессия 401 | Переход на вход с next; запись не создана. |
| Запоздалое чтение старого контекста | После выбора крытого корта №2 показаны его записи, а не записи предыдущего корта. |

Контроль QA через current_database()/current_user: test_kazan_courts /
kazan_test; 7 проверочных броней, 10 событий, включая исходную синтетическую
бронь. Для каждого из delay, badack, read503, write403, write503, write401
счётчик подтвердил ровно один POST. На корте №3, использованном для отказов,
броней нет. Шесть проверок фактических функций JS под UTC и America/New_York
прошли: отправка +03:00 и преобразование UTC, включая полночь, в дату Казани.
Проверочные скрипты не добавлены в репозиторий.

Снимок на вымышленных данных вне Git:
C:/Users/ruisk/.codex/visualizations/2026/09/29/01a0ed28-6d1b-77d0-b611-f18177682002/kazan-courts-stage08-workflow.jpg.

### Очистка QA, изоляция и сохранность — PASS

QA-процесс не завершился по SIGINT/SIGTERM; остановлен только его контейнер.
База затем удалена через DiscoverRunner.teardown_databases(), контейнер
удалён до окончательного полного прогона. Первая внешняя попытка teardown
с ошибочным текущим NAME=postgres получила отказ must be owner of database
postgres; база postgres не удалена. Исправлен только одноразовый проверочный
процесс: соединение закрыто, текущее имя восстановлено на test_kazan_courts
перед teardown. Код/settings проекта не менялись. Правильный teardown
успешен; после окончательного прогона снова подтверждено:

```text
test_kazan_courts exists: false
kazan_courts_dev exists: true
has_database_privilege('kazan_test','kazan_courts_dev','CONNECT'): false
Django development connection: kazan_courts_dev / kazan_dev
WEATHER_NETWORK_ENABLED: false
```

Development: 7 кортов, 3 клиента, 0 броней и 0 событий. SHA-256 всех
предметных полей и последовательностей совпал с исходным:
`1B4EA1E2C8B479C6C09321704D6177E525C949020A39E3B3641D03EA89333AFA`.
Обычные Compose web/db сохранены. Старый отдельный kazan-courts-postgres
не запускался и не изменялся: прежние ID, exited, loopback-порт и volume.
Его фактический FinishedAt — 6 октября, до этой работы; старый снимок
содержал 3 октября, поэтому совпадение этого устаревшего времени не заявляется.

Реальный Open-Meteo на этапе 08 не вызывался. QA использовал синтетические
прогнозы и guard реального transport; Django-тесты — WEATHER_NETWORK_ENABLED=0.
Реальная проверка остаётся результатом этапа 07.

### Состав diff и готовность

55 файлов вне scope совпали с исходными SHA-256. Проверка всех 61
Git-кандидата на четыре реальные локальные секрета из окружения контейнеров,
private keys, токены и запрещённые имена файлов прошла.
Новых файлов нет; env, локальные БД, временные файлы и QA-артефакты в Git
не добавлены. git diff --check прошёл. Известные предупреждения глобального
Git ignore и LF/CRLF не исправлялись.

HEAD = main = origin/main = fcacdeca; staging пустой.
Commit, push, PR, merge и GitHub Actions этапа 08: **NOT_RUN**.
Локальный итог: **PASS; готово к code review перед commit**.
