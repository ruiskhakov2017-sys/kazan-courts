# KazanCourts P01–P05 — фактические проверки

Дата: 13 ноября 2026, Europe/Moscow. Первоначальный независимый Review пройден с двумя открытыми вопросами (сообщение пользователя 14 ноября); повторный Review изменений CI/volume ещё не выполнен.
База: `3bd07ae8c5357faf54e38b89fa921c42b3dbdd8b`; исходная ветка `work` оставлена без изменений.
Worktree: `/workspace/kazan-courts-hw05`, ветка `feature/hw05-kazancourts-production`.
Источники получены из `KazanCourts_HW005_required_sources.zip`; оригиналы лежат в `sources/`.

## Изоляция и окружение

Только Compose-проект `kc-hw05-review`, отдельная сеть и тома с этим префиксом, loopback-порт 18005.
Никакие production DB/Kafka, n8n, погодные endpoint или VidHarbor не использовались.
Уникальные локальные секреты созданы генератором; значения не включены в отчёт/Review ZIP.
Docker 29.9.2, Compose 2.40.3, Python образа 3.14.8, Django 5.2.17,
psycopg 3.3.6, kafka-python 3.0.12, PostgreSQL 18.6 (финальная база Alpine), Kafka 4.2.2.
Проверены текущие repository pins; base image tags не digest-pinned, transitive pip dependencies не полностью закреплены — побитовая воспроизводимость не заявляется.
Облачная сборка использует дополнительный Compose build-secret `cloud_ca` из системного CA bundle.
Приложение и DB image собраны из изменённого worktree, не из старых onboarding-образов.

## Воспроизведение команд

В приложенном комплекте Review `logs/` — исходные stdout/stderr запусков, включая неудачные первые попытки.
Основные команды выполнялись из корня worktree. Обозначение `DC` ниже —
`docker compose -p kc-hw05-review -f compose.yaml -f /workspace/hw05-review/compose.cloud.json`;
`KAZAN_HTTP_PORT=18005` задан для всех этих команд. Сам cloud override приложен в ZIP.
Он добавляет только BuildKit secret, не меняет приложение, команды сервисов или образы.

```bash
python3 scripts/init_local_env.py
DC config --quiet
DC up --build -d --wait --wait-timeout 180
DC exec -T web python manage.py seed_demo_data
COMPOSE_FILE=compose.yaml:/workspace/hw05-review/compose.cloud.json KAZAN_HTTP_PORT=18005 \
  python3 scripts/check_production.py --project kc-hw05-review --port 18005
DC exec -T web python manage.py seed_demo_data
DC stop scheduler publisher consumer
set -a; source .env; set +a
DC exec -T -e DB_NAME=postgres -e DB_USER=kazan_test \
  -e DB_PASSWORD="$TEST_DB_PASSWORD" -e KAFKA_INTEGRATION=1 \
  web python manage.py test test_database bookings config.test_health --verbosity 2 --noinput
node --test bookings/tests/operator_queue_ui.test.js
```

Переменная DC — условное обозначение команды, не shell alias из поставки.
Для обычной локальной сборки cloud override не нужен; см. [operations](operations.md).
Workers остановлены перед Kafka-тестами: они используют выделенный broker/topic, poison-record не должен попадать фоновому consumer. Только тестовая роль CREATEDB, не development/admin.
После завершения тестов стенд останавливается с сохранением только тестовых томов.

## Результаты

| Проверка | Фактический результат | Журнал |
|---|---|---|
| Fresh Compose, migration gating, все сервисы | PASS после исправления прав init-скрипта | 28-final-alpine-clean-start |
| Реальные отказы DB/Kafka и recovery | PASS: ready200 →503 →200, live всегда200 | 32-alpine-final-smoke |
| Полный restart с сохранением volumes | PASS: down без удаления volumes, up, ready200 | 32-alpine-final-smoke |
| Сохранение demo data | PASS: до restart 7 кортов/3 клиента созданы, после — 0/0 новых | 31-alpine-seed-before, 33-alpine-seed-after |
| Unit health matrix, safe methods, no-cache, sanitized errors, close client | PASS, 6 тестов на host Python3.12; затем повтор в образе с общим набором | 10-health-unit-fixed |
| Полный Django + реальная Kafka | PASS на финальной Alpine-базе: 221 тест за 53.659s, без пропусков; оба real Kafka test — ok | 35-alpine-django-all |
| JS регрессии operator queue | PASS, 4 теста | 06-js-tests |
| check/migrate/makemigrations, environment overrides | PASS; DEBUG=0/hosts runtime override; test DB удалена, тестовая роль не имеет CONNECT к dev; login HTTP200 | 38-alpine-final-checks |
| Секреты: текущие файлы, diff, logs, config/history/all image layers | PASS: целевой поиск четырёх значений и token/key signatures, все сохранённые слои двух образов | 39-final-secret-audit |
| Изменения исходной ветки | PASS: work/исходный HEAD, git status пустой | 22-final-state |
| P06–P13, deployment, внешний доступ, backup/restore | Not Run — вне scope | operations, backup-restore |

## Неудачные попытки и исправления

- `01-clean-start`: FAIL — bind-mounted PostgreSQL init script mode0600 не читается пользователем postgres. Исправление: отдельный DB Dockerfile с COPY --chmod=0755. Удалены только созданные этой попыткой тестовые volumes (`02`), чистый старт повторён (`03`).
- `04-health-failures`: отказ/восстановление DB и Kafka прошли; FAIL на кратковременном закрытии HTTP-соединения во время полного restart. Smoke harness теперь повторяет HTTPException и воспроизводит down/up с сохранением volumes. Финальный harness проверен целиком (`14`, затем `32` на Alpine).
- `05-seed`: FAIL из-за одновременной проверки остановленной тестовой DB. Исправлен порядок: итоговые seed выполнены до/после завершённого smoke (`13`, `15`). Ошибка не означает дефект seed.
- `09-health-unit`: FAIL — тест импортировал отсутствующий в kafka-python3.0.12 класс NoBrokersAvailable. Тест использует фактический KafkaError, исправленный набор PASS (`10`).
- `19-checks`: прямой `python -c` для environment не задавал DJANGO_SETTINGS_MODULE; FAIL команды проверки. Финальная команда явно задаёт settings module. Значения секретов в диагностике не выводились.

- `23-final-secret-audit`: FAIL на первоначальном Debian DB image. Диагностика слоёв (`25`) выявила стандартный ssl-cert-snakeoil.key, PEM-материал в libgnutls и похожую на AWS ID строку в libunistring. Значения не выводились. Выбрана официальная база postgres:18.6-alpine; только собственные тестовые тома пересозданы, финальные старт/отказы/regression повторены. Это изменение не является инструкцией in-place migration существующей Debian PostgreSQL.

- `29-alpine-secret-audit`: false positive AWS-like substring в бинарной Unicode-таблице libunistring; диагностика `30/36` установила отсутствие границ самостоятельного токена. Поиск AWS ID уточнён границами `[A-Za-z0-9]`; полный поиск четырёх сгенерированных значений и PEM private key headers сохранён. Финальная проверка слоёв — `37/39`.

## Границы доказательств

Health/ready не проверяют lag, schema drift, зависшую SQL-сессию, worker progress, уведомления пользователя, внешнюю погоду или доступ по HTTPS.
Реальные Kafka-тесты проверяют существующий outbox/consumer и повторную доставку внутри изолированного брокера; все booking regression assertions сохранены.
Проверка секретов ищет четыре сгенерированных значения и распространённые сигнатуры credentials. Это не доказательство отсутствия всех возможных секретов во всей истории Git/внешних хранилищах.
Существующий CI bootstrap password — синтетический пароль временной CI-БД, не production credential; workflow на GitHub не запускался.
Локальный runserver сохранён. Полный production deployment, security audit и итоговый CP5 этим этапом не закрыты.
Commit/push/PR/merge/deployment не выполнялись. Итог передаётся для независимого Review.


## Повторный Review 14 ноября: CI и PostgreSQL

Подробный [отчёт изменений и границ](ci-review.md), [риск старых volumes](postgresql-volume-review.md).
Предыдущие строки PASS относятся к исполнению в Codex, а не GitHub Actions.

| Проверка новой версии | Codex | GitHub Actions |
|---|---|---|
| Workflow actionlint 1.7.7, Python syntax, раздельный volume | PASS | Not Run |
| Негативные тесты CI evidence/safety gates | PASS, 12 тестов | Not Run |
| Полный cold Compose CI orchestration | PASS: prepare=0/run=0/finalize=0, свежий kc-hw05-ci-codex-20261114-b | Not Run |
| Реальные health/readiness failures/recovery | PASS: live200, ready200→503→200 для DB и Kafka, сохранённые данные после down/up | Not Run |
| Django JSON discovery/execution и реальные Kafka tests | PASS: discovered=221, tests_run=221, failures/errors/skipped/expected_failures/unexpected_successes=0 | Not Run |
| JS TAP counts, отсутствие skips | PASS: tests=4/pass=4, fail/cancelled/skipped/todo=0 | Not Run |
| Temporary secrets audit всех образов, безопасные артефакты | PASS в явно указанном scope; generic signatures собственных образов, temporary values всех образов | Not Run |
| Cleanup новых ресурсов и секретного файла | PASS: containers/networks/volumes/local images отсутствуют; runtime.env/raw удалены | Not Run |
| Перенос существующего Debian-volume на Alpine | Not Run; не разрешён | Not Run |

Первый Codex CI run: prepare=0, run=0, finalize=1, общий FAIL. Причина — PEM-материал upstream libgnutls официального Kafka. Подробные raw-логи были удержаны и удалены; остался безопасный summary. Для unchanged upstream Kafka явно выделен scope временных генерируемых значений, остальные generic проверки сохранены. Дополнительный негативный тест подтверждает отказ при реальной утечке временного значения в этом режиме. Полный повторный run выполняется на новом project; результат не подменяется успешным первым тестовым этапом.

В настоящей задаче commit/push/PR/merge/deployment/dispatch не выполнялись. Прежний архив Review был ранее загружен в отдельную отчётную ветку по предыдущему поручению; он не содержит новую версию CI.


Финальный Codex CI run (`cloud-run-b.log`): **PASS**. Машинные результаты и аудированные stdout/stderr находятся в `ci-results/`, включая `django-results.json`, `js-tests.log`, `health-failure-recovery.log`, `secret-audit.log`, `cleanup.log` и `summary.txt`.
Первый FAIL run оставлен отдельно (`first-run-summary.txt`); не смешан с успешным повтором. `unit.log` содержит ожидаемые строки FAIL внутри негативных сценариев, но unittest-итог **12 tests, OK**.
`actionlint` официальный checksum проверен; workflow lint — PASS. Сводка не утверждает успешный GitHub Actions run: для GitHub всё ещё Not Run.

## Первый GitHub-hosted запуск и исправление cleanup (14 ноября)

Коммит `92393d86ba8a44ea465608e377f8a3fbf516bafb` отправлен только в feature-ветку, создан PR №19.
Реальные GitHub-hosted runs: push `43632011774`, PR `43632061403` — **FAIL** на проверке остаточных ресурсов после cleanup. Фазы сборки/отказов/тестов и аудит временных секретов прошли; конечный FAIL не подменяется PASS. Подробные raw-логи по политике удержаны, безопасный artifact summary сохранён.

Добавлено явное удаление только тегов build images с уникальным CI project prefix (без force, без удаления общих base image IDs), а также безопасная диагностика категории оставшихся ресурсов. До удаления namespace проверяется на отсутствие существующих ресурсов. В Codex: 14 gate tests и реальная проверка удаления временного тега с сохранением исходного Kafka image — PASS; actionlint — PASS. Итог повторного hosted run будет указан отдельно в PR; предыдущие строки Not Run относятся к снимку до отправки.

Merge, deployment и работа с действующими данными не выполнялись.
