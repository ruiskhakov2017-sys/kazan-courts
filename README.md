# KazanCourts

Учебный сервис бронирования теннисных кортов. Сотрудник входит в систему, читает корты, клиентов и расписание, проверяет погоду и подтверждает готовность открытого покрытия. Серверный API создаёт, переносит, отменяет и архивирует брони с атомарной записью истории. На рабочем экране доступны погодные блоки, ручная перепроверка и управление просушкой; полные формы бронирования — этап 08. Требования и архитектура находятся в docs/.

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

После миграций и создания сотрудника откройте http://127.0.0.1:8000/: анонимный запрос перенаправляется на `/login/`. Django development server слушает `0.0.0.0:8000` внутри контейнера, порт опубликован только на `127.0.0.1` Windows.

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

`bookings.0001_initial` подключает расширение PostgreSQL `btree_gist` и создаёт модели `Court`, `Customer`, `Booking`, `BookingEvent`; `0002_booking_no_overlap` добавляет запрет пересечений действующих броней одного корта. Django также применяет стандартные миграции `contenttypes`, `auth` и `sessions`: пользователь нужен для входа и ссылок на автора действий, сессии хранятся в PostgreSQL. Модели и миграции bookings на этапе 04 не изменяются.

`seed_demo_data` создаёт семь синтетических кортов (три крытых и четыре открытых) и трёх вымышленных клиентов. Команда не создаёт пользователей, брони или события. Повторный запуск распознаёт записи по демонстрационным именам, не создаёт дубликаты и не перезаписывает их изменённые поля. Переименованную запись команда распознать не сможет; при неоднозначном совпадении она завершится с ошибкой без частичного заполнения.

Ограничения БД проверяют обязательные связи, допустимые значения, время окончания после начала, длительность от часа и десятиминутную сетку. Интервалы имеют вид `[начало, конец)`: соседние брони разрешены, отменённые не занимают время. `Booking` хранит текущее состояние, `BookingEvent` — снимки до и после действия. Создание и перенос проверяют будущее время, календарный месяц и сезон. Перенос, отмена и архивирование сохраняют изменение и событие вместе; физического удаления через API нет. Модели и миграции bookings на этапах 05–06 не меняются.

### Создание сотрудника и вход

После миграций один раз создайте сотрудника в development-базе:

```powershell
docker compose exec web python manage.py create_employee
if ($LASTEXITCODE -ne 0) { throw "Сотрудник не создан: прочитайте сообщение команды." }
```

Команда интерактивно запросит пароль дважды со скрытым вводом и создаст `admin` с `is_active=True`, `is_staff=True`, `is_superuser=False`. Не передавайте пароль в аргументах команды и не записывайте его в репозиторий. Если `admin` или активный сотрудник уже существует, команда завершится отказом без изменения пароля или прав. При повторных запусках используйте существующую учётную запись; создание сотрудника не требуется после каждого запуска контейнеров. Для осознанной смены пароля используйте интерактивную команду `docker compose exec web python manage.py changepassword admin`.

Откройте http://127.0.0.1:8000/login/, войдите как `admin` и проверьте семь синтетических кортов, трёх клиентов и выбор корта/даты. При отсутствии броней показывается пустое расписание. Даты и время отображаются по Казани. Кнопка «Выйти» завершает сессию через POST с CSRF-защитой.

Экран использует Django Templates и обычный JavaScript; CSS и JS обслуживаются `runserver` в текущем локальном режиме. После изменения файлов требуется пересборка образа. Регистрации клиентов, Django admin-панели, создания или изменения броней через интерфейс пока нет.

### API чтения

Все запросы используют сессию вошедшего сотрудника и читают только PostgreSQL:

| GET endpoint | Ответ |
|---|---|
| `/api/courts/` | `courts`: ID, название, тип, координаты (десятичные строки), покрытие и последняя проверка |
| `/api/customers/` | `customers`: ID, имя, телефон и email; необязательный email — пустая строка |
| `/api/schedule/?court_id=1&date=2026-07-01` | `court_id`, `date`, `time_zone`, `bookings`: ID брони, корта и клиента, начало, конец, статус |

Для расписания обязательны положительный `court_id` и дата `YYYY-MM-DD`. Дата определяет сутки в `Europe/Moscow`; включены все действующие брони, пересекающие эти сутки, в том числе начавшиеся накануне. Интервалы сравниваются как `[начало, конец)`. Отменённые и архивированные брони в этот список не входят. Время JSON передаётся в ISO 8601 с часовым поясом, в интерфейсе отображается по Казани.

API чтения возвращает JSON-ошибки `401` без входа, `403` без допуска сотрудника, `400` при неверных параметрах, `404` для отсутствующего корта и `503` при недоступной БД. Эти три endpoint поддерживают только GET; другие методы отклоняются (`405`, либо предварительный отказ CSRF для незащищённого изменяющего запроса). Пустое расписание возвращается с `200` и `bookings: []`. Ответы с данными сотрудника не кешируются. Расписание показывает занятые интервалы; правила создания проверяются отдельно серверной операцией.

### Создание брони через API

`POST /api/bookings/` принимает `Content-Type: application/json`, сессию активного сотрудника и штатный CSRF-токен. Для браузерного JSON-запроса токен передаётся заголовком `X-CSRFToken`. Сотрудник и статус назначаются сервером; дополнительные поля запроса отклоняются.

```json
{
  "customer_id": 1,
  "court_id": 1,
  "starts_at": "2026-10-20T14:00:00+03:00",
  "ends_at": "2026-10-20T15:00:00+03:00"
}
```

Это пример формата: ID должны существовать, а даты при реальном вызове должны подходить текущему серверному времени. ID — положительные целые числа JSON. Время передаётся в ISO 8601 с явным часовым поясом (`+03:00`, `Z` или другой offset), затем нормализуется по Казани. Сервер проверяет телефон клиента, будущее начало, конец после начала, минимум 60 минут и обе десятиминутные границы без секунд/микросекунд; неверное время не округляется.

Месячный предел вычисляется от текущего момента по Казани: плюс один календарный месяц с сохранением местного времени и переносом на последний существующий день, если нужного дня нет. Например, `31 января 14:20 → 28 февраля 14:20` (29 февраля в високосный год). И начало, и конец укладываются в предел; конец ровно на границе разрешён. Если предел оказался между десятиминутными отметками, он не округляется вверх.

Крытые корты доступны по сезонному правилу круглый год. Для открытых весь интервал должен лежать в `[1 мая 00:00, 1 ноября 00:00)` по Казани; окончание ровно `1 ноября 00:00` разрешено. Создание и перенос также проверяют состояние покрытия и погодные блоки; недоступный прогноз разрешает оформление с предупреждением и сохранённой отметкой о перепроверке.

| Результат | Ответ |
|---|---|
| Бронь и история сохранены | `201`, объект `booking` с ID, связями, временем UTC в ISO 8601, статусом, автором, причиной отмены, архивом и отметками создания/изменения |
| Неверный JSON, типы/поля запроса | `400`, `invalid_payload`, сообщения по полям при наличии |
| Нарушены правила брони | `400`, `invalid_booking`, сообщения по полям |
| Клиент / корт не найден | `404`, `customer_not_found` / `court_not_found` |
| Пересечение действующих броней | `409`, `time_conflict` |
| Нет входа / допуска сотрудника | `401` / `403` |
| Другой HTTP-метод | `405`, `Allow: POST` (незащищённый изменяющий запрос может раньше получить CSRF `403`) |
| Ошибка БД | `503`, общее сообщение без SQL |

Операция в `bookings/services.py` явно записывает `Booking` и один `BookingEvent(created)` внутри одного `transaction.atomic()`. У события `before=null`, `after` — снимок сохранённой брони, `actor` — сотрудник. Ошибка истории откатывает бронь; ответ `201` формируется после успешного завершения транзакции. Предварительная проверка занятости дополняется существующим PostgreSQL exclusion constraint: он разрешает соседние интервалы и окончательно отклоняет конфликт при гонке. Строка Court целиком не блокируется. Прямой ORM/SQL обходит правила операции и автоматическую запись истории; все пользовательские создания должны идти через этот сервис.

Формы создания в рабочем экране пока нет; полный сценарий интерфейса предусмотрен этапом 08. Воспроизводимая проверка POST, авторизации, CSRF, сохранения/отката истории и конкуренции выполняется следующим прогоном изолированных тестов.

### Перенос, отмена, архив и история через API

Все изменяющие запросы требуют сессию активного сотрудника, JSON-объект и штатный CSRF-токен. API принимает только перечисленные поля; клиент, корт, первоначальный автор и статус не назначаются входным JSON.

| Метод и адрес | Вход | Результат |
|---|---|---|
| `POST /api/bookings/<id>/reschedule/` | `starts_at`, `ends_at`, необязательный текст `reason` | Перенос времени будущей действующей брони; клиент и корт сохраняются |
| `POST /api/bookings/<id>/cancel/` | `{}` или текст `reason` | Отмена будущей действующей брони; интервал освобождается |
| `POST /api/bookings/<id>/archive/` | `{}` или текст `reason` | Архивирование отменённой записи, включая прошлую |
| `GET /api/bookings/?court_id=1&date=2026-07-02` | Корт и дата по правилам GET расписания | Действующие и отменённые брони, пересекающие сутки по Казани, без архивированных |
| `GET /api/bookings/<id>/history/` | ID брони | `booking_id` и `events`: тип, сотрудник, время, причина, снимки до/после; архивированные записи доступны |

Пример JSON переноса (ID и даты нужно подобрать для существующей будущей брони):

```json
{
  "starts_at": "2026-10-20T16:00:00+03:00",
  "ends_at": "2026-10-20T17:00:00+03:00",
  "reason": "Согласованное изменение времени"
}
```

Успешное действие возвращает `200` и сохранённый объект `booking` в том же формате, что создание. Перенос повторно проверяет телефон клиента и все правила нового времени; собственная прежняя бронь исключается из предварительного поиска конфликта. PostgreSQL остаётся окончательной защитой от пересечений.

Для изменения сервис блокирует только строку этой Booking через `select_for_update()` внутри короткого atomic. Серверный now фиксируется после получения блокировки; начавшуюся бронь переносить или отменять нельзя. Изменение и одно событие истории сохраняются вместе. `created_by` и `created_at` сохраняются, новый сотрудник записывается в `actor` события. Ошибка истории откатывает изменение. Старые события не редактируются; отклонённый запрос не записывается как успешное действие.

При PostgreSQL deadlock (`40P01`) сервис один раз повторяет всю операцию после отката, включая допуск сотрудника, чтение/блокировку брони, серверное время и проверки. Всего допускаются две попытки. Повторный deadlock и другие ошибки БД дают `503`; конфликт времени по-прежнему даёт `409`. Повтор не дублирует бронь или событие откатившейся попытки.

Причина необязательна, должна быть строкой; крайние пробелы удаляются. При отмене причина сохраняется в Booking и BookingEvent, при переносе/архиве — в событии. Повторная отмена или архивация, перенос отменённого/архивированного, архив действующего и изменение начавшегося возвращают `409` без новых событий. Перенос на точно прежний интервал возвращает `400`, `no_change`. Неверные данные — `400`, отсутствующая бронь — `404`, временной конфликт — `409`, ошибка БД — `503`; SQL в ответ не включается.

Расписание GET `/api/schedule/` показывает только действующие интервалы с погодными отметками. Рабочий список GET `/api/bookings/` также показывает отменённые до архивации; история остаётся доступна по ID после архива. DELETE не поддерживается. Полный браузерный сценарий создания, переноса и отмены относится к этапу 08.

### Погода и готовность покрытия — этап 07

После обновления кода выполните обычные сборку и `python manage.py migrate` через Compose, как в начале README. Миграция `0003_booking_weather` добавляет две погодные отметки Booking, допустимый тип события `weather_checked` и ограничения допустимых значений. Старые открытые брони получают `unknown`, крытые — `not_applicable`; предыдущие события истории сохраняются без переписывания.

Клиент Open-Meteo отделён от правил. Одна синтетическая точка Казани `55.793000, 49.123000` задаёт общий прогноз для открытых кортов. Сутки по `Europe/Moscow` разбиты на `[00:00,06:00)`, `[06:00,12:00)`, `[12:00,18:00)`, `[18:00,00:00 следующих суток)`. Дождь в одном часу запрещает весь соответствующий блок, включая частичное пересечение брони. Облачность и туман сами по себе не дают дождевого запрета.

Дождевые коды: `51,53,55,56,57,61,63,65,66,67,80,81,82`. Коды без дождевого запрета: `0,1,2,3,45,48`. Остальные, включая снег и грозы `95/96/97/99`, означают `unknown` для текущего правила, как и недостающие часы или ошибка сервиса. Точная таблица и первичные источники — `docs/design.md`.

**Реальная сеть по умолчанию выключена.** `WEATHER_NETWORK_ENABLED` отсутствует в обычном Compose и CI, поэтому адаптер не отправляет запросы и сообщает неизвестный прогноз. Значение `1` включает сеть только в процессе, которому эта переменная явно передана; это следующий отдельно разрешаемый шаг, а не команда для автоматического выполнения. Не включайте его при воспроизводимых тестах. Тесты используют подготовленные ответы и не зависят от текущей погоды.

После разрешения адаптер использует HTTPS Forecast API с `hourly=weather_code`, `timezone=Europe/Moscow`, `forecast_days=16`, таймаутом 5 секунд и ограничением ответа 1 MiB. Полученный горизонт может быть короче календарного месяца: отсутствующие часы остаются неизвестными. Автоматических сетевых повторов нет; ошибка ответа или таймаут дают предупреждение, а не сухую погоду.

| Метод и адрес | Вход | Результат |
|---|---|---|
| `GET /api/weather/?court_id=4&date=2026-10-08` | Корт и дата | Четыре блока, время попытки и предупреждения; для крытого — `not_applicable`, без сетевого обращения |
| `POST /api/weather/recheck/` | `{}` | Проверка будущих действующих открытых броней; список затронутых записей, блоков и вымышленных контактов |
| `POST /api/courts/<id>/surface/close/` | `{}` | Закрытие готового открытого корта на просушку |
| `POST /api/courts/<id>/surface/ready/` | `{}` | После осмотра перевод из просушки в готовое состояние и время проверки |

POST требует сессию сотрудника, JSON и CSRF; дополнительные поля запрещены. Раздел на рабочем экране использует эти действия. Закрытие/подтверждение неподходящего состояния даёт `409`, неверные параметры — `400`, отсутствующий корт — `404`. Техническое обслуживание не снимается подтверждением просушки.

`weather_status` в Booking: `not_applicable`, `clear`, `unknown`, `blocked`; `weather_checked_at` — время последней попытки, а не гарантия актуальной погоды. `needs_weather_recheck` вычисляется для действующих `unknown`/`blocked`. API создания и переноса возвращает `warnings`; журнал хранит новые погодные поля в снимках.

Ручная перепроверка получает прогноз до транзакции, затем атомарно сохраняет отметки и события `weather_checked` для выбранного набора. Она не отменяет и не переносит брони. Сотрудник видит имя и телефон, связывается с клиентом и применяет согласованное действие. Неудачная запись истории откатывает весь набор.

Готовность покрытия и прогноз независимы. Пока Court имеет `drying` или `maintenance`, новая бронь запрещена. При неизвестном прогнозе физически осмотренное покрытие можно подтвердить с предупреждением. Дождь текущего блока запрещает подтверждение; будущий дождливый блок продолжает запрещать соответствующие брони после подтверждения.

В транзакции создания/переноса выбранная Court читается через `FOR SHARE`, а при ручном изменении — через `FOR NO KEY UPDATE`. Несколько бронирований совместно читают Court; закрытие ждёт завершения читателей или завершившееся закрытие становится видно новой брони. При переносе порядок — Court, затем Booking; `now` читается после ожиданий. Прогноз получается вне транзакции и повторно не запрашивается при deadlock.

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

Одноразовый контейнер использует образ `web` и тестовую роль. Django сам создаёт `test_kazan_courts` и применяет в ней миграции, включая `btree_gist` и sessions. Тест `test_database.py` доказывает имя базы и пользователя. Тесты проверяют прежние модели, ограничения, заполнение, чтение и вход, а также правила создания, POST/CSRF и атомарность истории. Конкурентный `TransactionTestCase` использует два независимых соединения: оба завершают реальную проверку свободного времени до INSERT, результат — `201`/`409`, одна действующая бронь и одно событие. Сценарий повторяется трижды; смежные конкурентные запросы получают два `201`. В выводе видны тестовая база/роль и разные PostgreSQL backend PID. Затем Django удаляет базу. Ожидаются `OK` и сообщения о создании и удалении test database. При аварийном прерывании база может остаться; выясните причину перед отдельным решением об удалении.

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
```

Если сотрудник ещё не создан в этой development-базе, выполните интерактивно:

```powershell
.\.venv\Scripts\python.exe manage.py create_employee
if ($LASTEXITCODE -ne 0) { throw "Сотрудник не создан: прочитайте сообщение команды." }
```

В `.venv` действует та же учётная запись `admin` и правила создания из раздела Compose, но базы независимы: пользователь Compose не переносится в прежнюю локальную базу. Не повторяйте команду, если сотрудник уже существует.

```powershell
.\.venv\Scripts\python.exe manage.py runserver --noreload 127.0.0.1:8000
```

Откройте http://127.0.0.1:8000/ в браузере и войдите как сотрудник. Для остановки сервера нажмите Ctrl+C.

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
