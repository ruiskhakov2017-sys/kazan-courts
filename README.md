# KazanCourts

Учебный сервис бронирования теннисных кортов. В интерфейсе пока доступна только стартовая страница. В базе предусмотрены корты, клиенты, брони и события их истории. Требования и архитектура находятся в docs/.

## Запуск через Docker Compose

Нужны PowerShell 7 и запущенный Docker Desktop с Linux containers. Все команды выполняются из корня репозитория. Порт `127.0.0.1:8000` должен быть свободен. Python на Windows для этого режима не требуется: образ `web` содержит Python 3.14.8 и зависимости из `requirements.txt`.

### Секреты в текущем сеансе PowerShell

При первой подготовке задайте три разных пароля через скрытый ввод и создайте временный ключ Django:

```powershell
$env:POSTGRES_PASSWORD = [pscredential]::new("postgres", (Read-Host "Bootstrap/admin пароль новой Compose-базы" -AsSecureString)).GetNetworkCredential().Password
$env:DEV_DB_PASSWORD = [pscredential]::new("kazan_dev", (Read-Host "Пароль kazan_dev для Compose" -AsSecureString)).GetNetworkCredential().Password
$env:TEST_DB_PASSWORD = [pscredential]::new("kazan_test", (Read-Host "Пароль kazan_test для Compose" -AsSecureString)).GetNetworkCredential().Password
$env:DJANGO_SECRET_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
```

Сохраните пароли в личном менеджере паролей. В новом сеансе задавайте прежние пароли: изменение переменных не меняет пароли ролей в существующем volume. Compose требует непустые значения. Секреты передаются в окружение контейнеров; не выводите переменные, полный `docker inspect` или развёрнутый `docker compose config`.

### Сборка и запуск

```powershell
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Некорректная конфигурация Compose." }
docker compose up --build -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw "Compose не запустился." }
docker compose ps
docker compose exec web python manage.py check
docker compose exec web python -m pip check
```

Откройте http://127.0.0.1:8000/. Django development server слушает `0.0.0.0:8000` внутри контейнера, порт опубликован только на `127.0.0.1` Windows.

В Compose два сервиса — `web` и `db` — на стандартной сети проекта. `db` использует `postgres:18.6`, не публикует порт на Windows и хранит данные в отдельном volume `kazan-courts_compose_postgres_data`, смонтированном в `/var/lib/postgresql`. Существующие `kazan-courts-postgres` и `kazan-courts-postgres-data` относятся к прежнему локальному режиму.

При первой инициализации пустого volume `docker/postgres/init.sh` создаёт:

- роль `kazan_dev`: LOGIN, без SUPERUSER, CREATEROLE и CREATEDB;
- роль `kazan_test`: LOGIN и CREATEDB, без SUPERUSER и CREATEROLE;
- базу `kazan_courts_dev` с владельцем `kazan_dev`;
- запрет доступа PUBLIC и роли `kazan_test` к development-базе.

Init-скрипт выполняется только на пустом volume. TCP healthcheck проверяет готовность `db`; `web` запускается после состояния healthy. Схема приложения создаётся отдельной командой миграции, запуск контейнеров её автоматически не меняет.

Проверьте реальное подключение Django:

```powershell
docker compose exec web python manage.py shell -c 'from django.db import connection; c = connection.cursor(); c.execute("SELECT current_database(), current_user;"); print(c.fetchone()); c.close(); connection.close()'
```

Ожидается `('kazan_courts_dev', 'kazan_dev')`. Django получает `DB_HOST=db` и `DB_PORT=5432` для соединения внутри сети Compose.

### Миграции и демонстрационные данные

Продолжайте только после подтверждения development-базы и пользователя в SQL-проверке выше. Сначала посмотрите план, затем примените миграции и заполните базу:

```powershell
docker compose exec web python manage.py migrate --plan
if ($LASTEXITCODE -ne 0) { throw "Не удалось получить план миграций." }
docker compose exec web python manage.py migrate
if ($LASTEXITCODE -ne 0) { throw "Миграции не применены." }
docker compose exec web python manage.py seed_demo_data
if ($LASTEXITCODE -ne 0) { throw "Демонстрационные данные не загружены." }
docker compose exec web python manage.py migrate --check
if ($LASTEXITCODE -ne 0) { throw "Остались неприменённые миграции." }
docker compose exec web python manage.py makemigrations --check --dry-run
if ($LASTEXITCODE -ne 0) { throw "Модели и файлы миграций не согласованы." }
```

`bookings.0001_initial` подключает расширение PostgreSQL `btree_gist` и создаёт модели `Court`, `Customer`, `Booking`, `BookingEvent`; `0002_booking_no_overlap` добавляет запрет пересечений действующих броней одного корта. Django также применяет стандартные миграции `contenttypes` и `auth`: пользователь нужен для ссылок на создателя брони и автора события. Вход сотрудника будет реализован отдельно.

`seed_demo_data` создаёт семь синтетических кортов (три крытых и четыре открытых) и трёх вымышленных клиентов. Команда не создаёт пользователей, брони или события. Повторный запуск распознаёт записи по демонстрационным именам, не создаёт дубликаты и не перезаписывает их изменённые поля. Переименованную запись команда распознать не сможет; при неоднозначном совпадении она завершится с ошибкой без частичного заполнения.

Ограничения БД проверяют обязательные связи, допустимые значения, время окончания после начала, длительность от часа и десятиминутную сетку. Интервалы имеют вид `[начало, конец)`: соседние брони разрешены, отменённые не занимают время. `Booking` хранит текущее состояние, `BookingEvent` — снимки до и после действия. Операции создания, переноса, отмены и архивации вместе с записью истории появятся на этапах 05–06. Проверки будущего времени, календарного месяца и сезона также относятся к будущим операциям.

### Изолированные тесты в Compose

Перед каждым прогоном явно запустите `db` и дождитесь healthy. Пароль тестовой роли передаётся через временную переменную окружения, без значения в аргументах команды:

```powershell
docker compose up -d --wait --wait-timeout 120 db
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL db не готов к тестам." }
$env:DB_PASSWORD = $env:TEST_DB_PASSWORD
try {
    docker compose run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD web python manage.py test test_database bookings --verbosity 2 --noinput
    if ($LASTEXITCODE -ne 0) { throw "Тесты PostgreSQL и приложения не прошли." }
}
finally {
    Remove-Item Env:DB_PASSWORD
}
```

Одноразовый контейнер использует образ `web` и тестовую роль. Django сам создаёт `test_kazan_courts` и применяет в ней миграции, включая `btree_gist`. Тест `test_database.py` доказывает имя базы и пользователя, тесты приложения проверяют модели, ограничения PostgreSQL и демонстрационное заполнение. Затем Django удаляет базу. Ожидаются `OK` и сообщения о создании и удалении test database. При аварийном прерывании база может остаться; выясните причину перед отдельным решением об удалении.

После теста проверьте удаление базы и сохранение запрета CONNECT:

```powershell
@'
SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'test_kazan_courts') AS test_database_exists;
SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'kazan_courts_dev') AS development_database_exists;
SELECT has_database_privilege('kazan_test', 'kazan_courts_dev', 'CONNECT') AS test_can_connect_to_dev;
'@ | docker compose exec -T db psql --no-psqlrc --set ON_ERROR_STOP=1 --username postgres --dbname postgres
```

Ожидается `f`, `t`, `f`. Test database вручную не создавайте.

### Остановка и повторный запуск

```powershell
docker compose down
docker compose up -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw "Повторный запуск Compose не удался." }
```

Обычный `down` удаляет контейнеры и стандартную сеть, сохраняя named volume. Пароли и созданная development-база остаются прежними. `down --volumes` удаляет данные и не используется для обычной остановки.

Код копируется в образ. После изменения кода выполните `docker compose up --build -d --wait --wait-timeout 120`.

Если первоначальный сеанс PowerShell закрыт, а контейнеры ещё существуют, их локальные параметры можно восстановить без вывода значений:

```powershell
$dbId = docker ps --all --quiet --filter "label=com.docker.compose.project=kazan-courts" --filter "label=com.docker.compose.service=db"
$webId = docker ps --all --quiet --filter "label=com.docker.compose.project=kazan-courts" --filter "label=com.docker.compose.service=web"
if (-not $dbId -or -not $webId) { throw "После down восстановите пароли из личного менеджера паролей." }
$dbEnvironment = @(docker inspect --format '{{json .Config.Env}}' $dbId | ConvertFrom-Json)
foreach ($variableName in @("POSTGRES_PASSWORD", "DEV_DB_PASSWORD", "TEST_DB_PASSWORD")) {
    $entry = $dbEnvironment | Where-Object { $_.StartsWith($variableName + "=") }
    [Environment]::SetEnvironmentVariable($variableName, $entry.Substring($variableName.Length + 1), "Process")
}
$webEnvironment = @(docker inspect --format '{{json .Config.Env}}' $webId | ConvertFrom-Json)
$env:DJANGO_SECRET_KEY = ($webEnvironment | Where-Object { $_.StartsWith("DJANGO_SECRET_KEY=") }).Substring("DJANGO_SECRET_KEY=".Length)
Remove-Variable dbEnvironment,webEnvironment,entry
```

Для поиска контейнеров в этом блоке не требуется повторный ввод секретов: используются labels контейнеров через `docker ps`. После `down` контейнеров уже нет, поэтому сохраните пароли до остановки и закрытия сеанса.

После работы закройте PowerShell или удалите переменные `POSTGRES_PASSWORD`, `DEV_DB_PASSWORD`, `TEST_DB_PASSWORD`, `DJANGO_SECRET_KEY`.

## Запуск в .venv: прежний локальный режим

Для этого режима нужны Python 3.14.8 и PowerShell 7. Django запускается локально в `.venv`, PostgreSQL 18.6 — в существующем контейнере `kazan-courts-postgres`. Его development-база и volume независимы от Compose.

Создайте `.venv`, если её ещё нет, затем установите зависимости:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

В `requirements.txt` зафиксированы Django 5.2.17 и `psycopg[binary]` 3.3.6. Активировать окружение не обязательно: команды ниже используют его Python напрямую.

## Первичная подготовка PostgreSQL

Этот раздел нужен только для нового локального окружения. Если контейнер `kazan-courts-postgres`, роли и development-база уже подготовлены, переходите к разделу «Development». Остановленный существующий контейнер запускается командой `docker start kazan-courts-postgres`.

Проверьте доступность Docker командой `docker version`. Для нового контейнера порт `127.0.0.1:5432` должен быть свободен. Создайте отдельный persistent volume и запустите сервер; bootstrap/admin пароль задайте через скрытый ввод:

```powershell
docker volume create kazan-courts-postgres-data
$env:POSTGRES_PASSWORD = [pscredential]::new("postgres", (Read-Host "Задайте bootstrap/admin пароль PostgreSQL" -AsSecureString)).GetNetworkCredential().Password
try {
    docker run --detach --name kazan-courts-postgres `
        --publish 127.0.0.1:5432:5432 `
        --mount type=volume,source=kazan-courts-postgres-data,target=/var/lib/postgresql `
        --env POSTGRES_PASSWORD `
        postgres:18.6
    if ($LASTEXITCODE -ne 0) { throw "Не удалось запустить PostgreSQL." }
}
finally {
    Remove-Item Env:POSTGRES_PASSWORD
}
```

Для PostgreSQL 18 volume монтируется в `/var/lib/postgresql`. Данные сохраняются при остановке контейнера. Пароль передаётся Docker из окружения процесса; не выводите окружение или полный `docker inspect`, содержащий параметры контейнера.

Проверьте готовность; при первой инициализации повторите команду через несколько секунд, пока не появится `accepting connections`:

```powershell
docker exec kazan-courts-postgres pg_isready --host 127.0.0.1 --port 5432 --username postgres --dbname postgres
```

Откройте интерактивный `psql` внутри контейнера:

```powershell
docker exec --interactive --tty kazan-courts-postgres psql --no-psqlrc --set ON_ERROR_STOP=1 --username postgres --dbname postgres
```

В `psql` выполните SQL для новых ролей и development-базы:

```sql
CREATE ROLE kazan_dev LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS;
CREATE ROLE kazan_test LOGIN NOSUPERUSER NOCREATEROLE CREATEDB NOREPLICATION NOBYPASSRLS;
CREATE DATABASE kazan_courts_dev OWNER kazan_dev;
REVOKE ALL ON DATABASE kazan_courts_dev FROM PUBLIC;
REVOKE ALL ON DATABASE kazan_courts_dev FROM kazan_test;
```

Задайте разные пароли ролей. Сначала выполните одну команду и ответьте на оба скрытых запроса пароля:

```text
\password kazan_dev
```

Затем выполните следующую команду и задайте тестовый пароль:

```text
\password kazan_test
```

Завершите `psql` командой `\q`. Сохраните выбранные пароли в личном менеджере паролей. Команда `\password` позволяет не записывать открытый пароль в SQL или историю команд. Bootstrap/admin роль используется только для подготовки сервера, Django работает под отдельными ролями.

## Development

В PowerShell задайте все пять параметров подключения и временный ключ Django. Пароль `kazan_dev` вводится скрыто:

```powershell
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "5432"
$env:DB_NAME = "kazan_courts_dev"
$env:DB_USER = "kazan_dev"
$env:DB_PASSWORD = [pscredential]::new($env:DB_USER, (Read-Host "Пароль kazan_dev" -AsSecureString)).GetNetworkCredential().Password
$env:DJANGO_SECRET_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")

.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py shell -c 'from django.db import connection; c = connection.cursor(); c.execute("SELECT current_database(), current_user;"); print(c.fetchone()); c.close(); connection.close()'
```

`check` должен завершиться без ошибок. SQL-проверка реального соединения должна вывести `('kazan_courts_dev', 'kazan_dev')`. В PostgreSQL `current_user` пишется без скобок.

После подтверждения базы и пользователя примените схему и загрузите демонстрационные данные в этой независимой development-базе. Миграции и данные, применённые через Compose, сюда не переносятся:

```powershell
.\.venv\Scripts\python.exe manage.py migrate --plan
if ($LASTEXITCODE -ne 0) { throw "Не удалось получить план миграций." }
.\.venv\Scripts\python.exe manage.py migrate
if ($LASTEXITCODE -ne 0) { throw "Миграции не применены." }
.\.venv\Scripts\python.exe manage.py seed_demo_data
if ($LASTEXITCODE -ne 0) { throw "Демонстрационные данные не загружены." }
.\.venv\Scripts\python.exe manage.py migrate --check
if ($LASTEXITCODE -ne 0) { throw "Остались неприменённые миграции." }
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
if ($LASTEXITCODE -ne 0) { throw "Модели и файлы миграций не согласованы." }
.\.venv\Scripts\python.exe manage.py runserver --noreload 127.0.0.1:8000
```

Откройте http://127.0.0.1:8000/ в браузере. Для остановки сервера нажмите Ctrl+C.

## Изолированные тесты PostgreSQL

Откройте отдельный сеанс PowerShell в корне репозитория. Для тестов используйте `kazan_test` и служебную базу `postgres` как исходное подключение:

```powershell
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "5432"
$env:DB_NAME = "postgres"
$env:DB_USER = "kazan_test"
$env:DB_PASSWORD = [pscredential]::new($env:DB_USER, (Read-Host "Пароль kazan_test" -AsSecureString)).GetNetworkCredential().Password
$env:DJANGO_SECRET_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")

.\.venv\Scripts\python.exe manage.py test test_database bookings --verbosity 2 --noinput
```

`config/settings.py` задаёт `TEST.NAME = "test_kazan_courts"`. Django сам создаёт эту базу и применяет миграции. Тест из `test_database.py` проверяет `current_database()` и `current_user`, тесты `bookings` проверяют модели, ограничения и заполнение, затем Django удаляет базу. Ожидаются сообщения `Creating test database`, успешное завершение (`OK`) и `Destroying test database`.

Не создавайте test database вручную и не запускайте тесты под `kazan_dev`. Право `CREATEDB` у `kazan_test` позволяет создавать тестовую базу, а отзыв доступа `PUBLIC` и отсутствие `CONNECT` у тестовой роли защищают development-базу. Подготовка схемы тестовым runner выполняется внутри тестовой базы. После аварийного прерывания тестовая база может остаться: сначала проверьте её имя и владельца перед отдельным решением об удалении.

После теста выполните проверки только на чтение:

```powershell
@'
SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'test_kazan_courts') AS test_database_exists;
SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'kazan_courts_dev') AS development_database_exists;
SELECT has_database_privilege('kazan_test', 'kazan_courts_dev', 'CONNECT') AS test_can_connect_to_dev;
'@ | docker exec --interactive kazan-courts-postgres psql --no-psqlrc --set ON_ERROR_STOP=1 --username postgres --dbname postgres
```

Ожидаемые значения: `false`, `true`, `false` (в `psql`: `f`, `t`, `f`).

## Пароли и существующее локальное окружение

Все переменные выше действуют только в текущем сеансе PowerShell. Проект читает их напрямую; `.env` автоматически не загружается. Не сохраняйте реальные пароли или `DJANGO_SECRET_KEY` в репозитории.

В уже подготовленном Windows-окружении пароли ролей сохранены вне репозитория в `%LOCALAPPDATA%\KazanCourts\postgres-credentials.clixml`, с шифрованием для текущего пользователя Windows. Если этот файл существует, после выбора `DB_USER` вместо строки со скрытым вводом можно использовать:

```powershell
$localCredentials = @(Import-Clixml -LiteralPath (Join-Path $env:LOCALAPPDATA "KazanCourts\postgres-credentials.clixml"))
$env:DB_PASSWORD = ($localCredentials | Where-Object { $_.UserName -eq $env:DB_USER }).GetNetworkCredential().Password
Remove-Variable localCredentials
```

Такое хранилище читается тем же пользователем на том же компьютере. На новом компьютере используйте пароли, заданные при первичной подготовке. Не выводите содержимое хранилища или значение `DB_PASSWORD`; после работы закройте сеанс PowerShell либо удалите переменную командой `Remove-Item Env:DB_PASSWORD`.

Для остановки и повторного запуска сервера используйте `docker stop kazan-courts-postgres` и `docker start kazan-courts-postgres`. Не удаляйте volume, если нужны сохранённые данные.
