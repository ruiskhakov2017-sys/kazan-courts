# KazanCourts

Учебный сервис бронирования теннисных кортов. Сейчас доступна только стартовая страница. Требования и архитектура находятся в docs/.

## Окружение и зависимости

Нужны Python 3.14.8, PowerShell 7 и запущенный Docker Desktop с Linux containers. Все команды PowerShell выполняются из корня репозитория. Django запускается локально в `.venv`, PostgreSQL 18.6 — в отдельном Docker-контейнере. Compose на этом этапе не используется.

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
.\.venv\Scripts\python.exe manage.py runserver --noreload 127.0.0.1:8000
```

`check` должен завершиться без ошибок. SQL-проверка реального соединения должна вывести `('kazan_courts_dev', 'kazan_dev')`. В PostgreSQL `current_user` пишется без скобок.

Откройте http://127.0.0.1:8000/ в браузере. Для остановки сервера нажмите Ctrl+C. На текущем этапе нет моделей приложения; миграции в development-базе не запускайте.

## Изолированные тесты PostgreSQL

Откройте отдельный сеанс PowerShell в корне репозитория. Для тестов используйте `kazan_test` и служебную базу `postgres` как исходное подключение:

```powershell
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "5432"
$env:DB_NAME = "postgres"
$env:DB_USER = "kazan_test"
$env:DB_PASSWORD = [pscredential]::new($env:DB_USER, (Read-Host "Пароль kazan_test" -AsSecureString)).GetNetworkCredential().Password
$env:DJANGO_SECRET_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")

.\.venv\Scripts\python.exe manage.py test test_database --verbosity 2
```

`config/settings.py` задаёт `TEST.NAME = "test_kazan_courts"`. Django сам создаёт эту базу, тест из `test_database.py` проверяет `current_database()` и `current_user`, затем Django удаляет базу. Ожидаются сообщения `Creating test database`, один успешный тест (`Ran 1 test`, `OK`) и `Destroying test database`.

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
