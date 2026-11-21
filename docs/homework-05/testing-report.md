# Актуальный статус

Итог публикации и GitHub Actions: [acceptance.md](acceptance.md). Ниже сохранены датированные снимки подготовки и проверок; строки Not Run/без commit относятся к моменту этих снимков.

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

# Этап P06–P13 — 21 ноября 2026

Новая локальная ветка `feature/hw05-kazancourts-p06-p13`, база main `ef412f7c87c694512eb365f2a31439a46656aebc`. Ни commit, ни push, ни PR/deployment этого этапа не выполнялись. Ниже — **новые результаты Codex**. Предыдущие результаты GitHub Actions относятся к другой версии P01–P05.

| Проверка | Фактический результат | Файл в Review evidence |
|---|---|---|
| Полный холодный Compose + monitoring, миграции/модели | PASS | summary.txt/build.log/start.log/model-drift.log |
| P01–P05 и новые unit observability | PASS: 228 Django tests, 0 failures/errors/skips/expected failures/unexpected successes, реальные Kafka tests | django-results.json/django-tests.log |
| JavaScript | PASS: 4/4, 0 skip/cancelled/todo | js-tests.log |
| CI safety gates | PASS: 14 тестов, включая отрицательные случаи | gates.log |
| JSON logs | PASS: actual Django/worker JSON records с timestamp/level/service/event/error/context | service-logs.log/full-http-web.log |
| HTTP→Outbox→Kafka→notification trace | PASS: 5 связанных этапов, общие trace/correlation IDs, проверены все parent edges, отдельные publisher/consumer services | full-trace.json |
| Метрики | PASS: count увеличивается, HTTP404 добавляет error; durations/Prometheus endpoint проверены | full-metrics.json/p06-p13.log |
| Dashboard | PASS в Chromium: 19 строк метрик, полная трасса, JS errors=[] | dashboard.png/browser-proof.json |
| Backup | PASS: pg_dump18.6, format custom PGDMP, 53805 bytes | full-backup-restore.json/full-dump-version.log/full-server-version.log |
| Restore | PASS: новая restore-db первоначально0 tables; pg_restore exit0; полный content+sequences hash совпал | full-restore-empty.log/full-pg-restore.log/full-restored-manifest.log |
| Возобновление незавершённой работы | PASS: broker ack до backup, notification отсутствовала; reconcile1 → новые Kafka/workers → 2 simulated notifications; повтор reconcile0 | full-reconcile.log/full-reconcile-idempotent.log/full-outcomes-restore-web.log |
| Проверка источника | PASS: reconcile отказан для исходного db даже с recovery flag | full-source-reconcile-refused.log |
| Восстановленное приложение | PASS: readiness200, чтение контрольной брони, новая indoor booking+history через настоящий HTTP201 | full-read-restore-web.log |
| Обновление версии/схемы | PASS: старое приложение ef412f7/0006 создало контрольную бронь; новая версия/0007 читает её и создаёт новую, ready200 | full-upgrade.json/full-previous-data.log/full-upgrade-migrate.log/full-read-upgrade-web.log |
| Secrets и cleanup | PASS: образ/история/слои + raw logs в заявленном scope; новые containers/volumes/network/build tags удалены; private env/raw/dump CI удалены | secret-audit.log/cleanup.log/summary.txt |
| GitHub Actions для P06–P13 | **Not Run**, workflow только подготовлен | нет нового hosted run URL |

Фактическая трасса: `b899d1dee31a44a09920ff1aedb67611`, correlation `evidence-e2e8841c4675d153`.
SHA256 dump: `57e1b443868b6c6467ee7d8d683d23f228df1472cfd26b093301d61408751db9`.
Fingerprint public rows и public sequences до/после restore: `b0cbb2854597a249f11513a7a200fbf02ce43b809b2d8a1c7b079eabd05cde37`.
Версия сервера: PostgreSQL18.6, x86_64 Linux/musl. Образы приложения/source target IDs записаны в full-upgrade.json.

## Команды воспроизведения

На свежем project CI helper создаёт собственный private runtime.env и ownership marker. Существующие ресурсы отвергаются.

```bash
export COMPOSE_PROJECT_NAME=kc-hw05-ci-your-unique-id
python3 scripts/ci_production.py prepare --state-dir /tmp/kazan-private --artifacts /tmp/kazan-safe
python3 scripts/ci_production.py run --state-dir /tmp/kazan-private --artifacts /tmp/kazan-safe
# Выполнять finalize даже при неуспешном run, сохраняя его исходный exit status:
python3 scripts/ci_production.py finalize --state-dir /tmp/kazan-private --artifacts /tmp/kazan-safe
```

Codex-прогон дополнительно использовал public CA build secret через session-only Compose override для нормальных/recovery/upgrade app images; TLS-проверка не отключалась. Ранний P01 commit для настоящей проверки обновления извлекается git archive; workflow теперь fetch-depth0. Synthetic weather fixture подменяет только адаптер в реальном WSGI instance; сетевой HTTP, PostgreSQL и Kafka настоящие. Session для synthetic employee создаётся test harness, пользовательские пароли не запрашиваются/не печатаются.

## Первый неуспешный запуск

`run-a.log`: Django/JS прошли, но P06–P13 proof получил **FAIL** на ожидании notification. Причина: изолированные Kafka poison/redelivery tests оставили тестовый topic, consumer сценария встретил его записи. Сценарии разделены: в новом, ownership-checked CI broker после регрессий очищается только тестовый topic перед созданием demo Outbox; рабочие brokers не трогаются. Исправленный полный `run-b.log`: prepare=0/run=0/finalize=0. Первый FAIL не обозначается успешным.

## Ограничения и статус Review

Все перечисленные операции выполнялись только на новых тестовых ресурсах. Существующие Debian/Alpine volumes, действующие данные и VidHarbor не изменялись. Private synthetic dump сохранён локально вне Git; публичный ZIP содержит только checksum/metadata и безопасные результаты.
Обновление проверено старым app image/0006 → новым image/0007 в отдельной DB, с CLI и реальным HTTP на новой версии. Rolling update/работа старых writers на новой схеме/автоматический rollback не проверялись; runbook требует остановки writers и согласованного восстановления.
Collector best-effort, telemetry может теряться при отказе. Не заявляется полная production readiness, приёмка ментора или завершение теории. Технические результаты P06–P13 подготовлены для независимого Review.
