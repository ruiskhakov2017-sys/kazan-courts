# KazanCourts — Homework 005, этап 1 (P01–P05)

Статус: подготовлено к независимому Review, не полная production readiness.
Источник: [Homework §5](sources/Mentor_Homework_005.md), [roadmap Level 5 A/B/C](sources/ai_native_engineering_roadmap.md).
Работа относится только к KazanCourts. Центральные документы видеопроекта и статусы обучения не изменяются.

## Архитектура и границы

`web → PostgreSQL`; `scheduler → PostgreSQL/outbox`; `publisher → Kafka → consumer → PostgreSQL`.
Существующие правила бронирования, погоды, topic, publisher и consumer сохранены.
Сервисы `db` и `kafka` не публикуют порты наружу. Web привязан к loopback.
Одна Compose-сеть на проект; два named volumes для PostgreSQL и Kafka.
`migrate` — одноразовая задача после готовности БД, перед всеми Django-процессами.
Мониторинг, structured logs, traces, dashboard относятся к следующим этапам.

## Матрица требований

| ID | Требование | Изменение / доказательство | Результат и граница |
|---|---|---|---|
| P01 | Весь локальный сценарий Compose | web, db, kafka, scheduler, publisher, consumer + migrate; чистый старт, restart и регрессии | PASS — см. [фактические проверки](testing-report.md) |
| P02 | Конфигурация без изменения бизнес-кода | `.env.example`, генератор `.env`; DEBUG, hosts, port, broker, weather; тест runtime override | PASS — см. testing-report |
| P03 | Секреты вне Git/образа/отчёта | `.env` 0600, уникальные значения; исключение из build context; целевая проверка diff/logs/image layers | PASS — см. testing-report; не аудит всех исторических credentials |
| P04 | Жизнь процесса | GET/HEAD `/health/live`, 200 даже при остановленных DB/Kafka | PASS — см. testing-report; не проверяет worker progress |
| P05 | Готовность выполнять работу | GET/HEAD `/health/ready`: SELECT 1 и Kafka cluster metadata; отказ 503 и восстановление 200 | PASS — см. testing-report; не проверяет миграционную схему, lag, доставку, работоспособность workers |
| P06 | Structured logs | Не начато | Not Run — вне этапа |
| P07 | Correlation ID | Не начато | Not Run — вне этапа |
| P08 | Сквозной trace | Не начато | Not Run — вне этапа |
| P09 | Metrics | Не начато | Not Run — вне этапа |
| P10 | Dashboard | Не начато | Not Run — вне этапа |
| P11 | Backup | Не начато | Not Run — [открытый артефакт](backup-restore.md) |
| P12 | Restore | Не начато | Not Run — [открытый артефакт](backup-restore.md) |
| P13 | Запуск/обновление/восстановление | Для этапа описан только запуск | Not Run в полном объёме — [operations](operations.md) |

## Environment

Compose читает `.env` из корня. На хосте Django сам `.env` не читает.
Переменные shell имеют приоритет над `.env`; не выводить `docker compose config` без `--quiet`.

| Переменная | Назначение / безопасный пример |
|---|---|
| POSTGRES_PASSWORD | Bootstrap/admin пароль локальной БД, генерируется |
| DEV_DB_PASSWORD | Пароль роли kazan_dev, генерируется |
| TEST_DB_PASSWORD | Пароль отдельной роли kazan_test, генерируется |
| DJANGO_SECRET_KEY | Django signing key, генерируется |
| DJANGO_DEBUG | `1` для локального запуска, `0` для проверки отключения debug |
| DJANGO_ALLOWED_HOSTS | `localhost,127.0.0.1`; список через запятую |
| KAZAN_HTTP_PORT | Порт loopback хоста; `8000`, в Review `18005` |
| KAFKA_BOOTSTRAP_SERVERS | Внутри Compose `kafka:9092`; адрес должен быть доступен всем Django-процессам |
| WEATHER_NETWORK_ENABLED | `0`; реальные запросы не разрешены в этом этапе |
| DB_HOST/DB_PORT | Compose задаёт `db`/`5432`; прямому запуску задавать явно |
| DB_NAME/DB_USER/DB_PASSWORD | Compose задаёт development DB/роль/пароль; для тестов только postgres/kazan_test/TEST_DB_PASSWORD |
| COMPOSE_PROJECT_NAME / `-p` | Пространство имён сети, контейнеров, томов; отдельное для каждого теста |

Имена DB/ролей закреплены текущим init-скриптом; изменение этих имён не заявлено.
Пароли задаются при первом создании volume; замена `.env` не меняет пароли существующей БД.
Генератор отказывается перезаписывать `.env`. Значения GENERATE_LOCALLY в шаблоне — placeholders, не готовая конфигурация.

## Secrets и build

Выбран прежний способ передачи через environment; файл не попадает в Git или образ.
Это не secret manager: администратор Docker/хоста видит environment процесса. Не публиковать `inspect`, полный Compose config, `.env` и dumps.
Образ копирует только код/шаблоны/static, PostgreSQL-образ на официальной базе postgres:18.6-alpine — только init-скрипт. Debian-база заменена после обнаружения стандартного snakeoil private key в её слоях; перенос существующей Debian-БД на Alpine не выполнялся и требует отдельного плана. Приложение работает под UID 10001.
Для cloud proxy поддержан необязательный BuildKit secret `cloud_ca`; CA подключается только на шаге pip, TLS-проверка сохраняется.
Это публичный сертификат доверия, не credential. На обычном Docker build secret не нужен.

## Health/readiness policy

Liveness не обращается к зависимостям, не требует входа; оба endpoint не кешируются и не принимают запись.
Readiness отвечает `{"status":"ready","checks":{"postgres":true,"kafka":true}}` либо 503 с булевыми состояниями.
Имена хостов, исключения, пароли в ответ не включаются. Kafka проверяется чтением metadata без создания сообщений.
Настроены connect timeout PostgreSQL 2s и Kafka bootstrap/request timeout 1.5s.
Это не общий deadline запроса: зависшая уже открытая SQL-сессия или DNS может потребовать отдельной политики.
Для локальной системы Kafka обязательна как часть полного фонового сценария. При её отказе web жив, бронирования по прежней логике могут сохраняться в outbox.
Readiness — сигнал оператору/будущему роутеру, не автоматический запрет API. Docker healthcheck использует liveness; он не доказывает автоматический restart unhealthy контейнера.

## Roadmap и решения

Этап даёт технические доказательства для A01–A04 (image/volume/network/Compose), A09–A11 (environment/secrets/migrations), C07 (health/readiness), B01/B03 (локальная сборка/тесты).
Он не закрывает весь Track A/B/C, CI/CD, deployment, TLS, rollback или понимание пользователя.
Автоматические миграции здесь выбраны для воспроизводимого локального стенда; план production-миграций и rollback остаётся P13.
Собственный design/выбор пользователя и защита не реконструируются задним числом. Это реализация агента в рамках прямого поручения и материал для Review.


## Дополнение повторного Review (14 ноября)

Первоначальная реализация независимо проверена; открытые вопросы CI и PostgreSQL рассмотрены в [ci-review](ci-review.md) и [volume review](postgresql-volume-review.md).
Для новых Alpine-стендов Compose использует `compose_postgres_alpine_data`, сохраняя старый Debian-volume отдельно. Автоматический перенос данных не выполняется.
Подготовлен hosted CI P01–P05; Codex и GitHub доказательства разделены в testing-report. Это не начало P06–P13, не новый observability/CI-CD deployment этап и не заявление об успешном Actions run.
