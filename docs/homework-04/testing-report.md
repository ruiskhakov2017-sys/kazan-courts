# Homework 004 — матрица доказательств и результаты проверок

Дата локальных проверок: 29 октября 2026. Статус этапа 6: **ожидает review**. Новая конфигурация GitHub Actions ещё не запущена; прежний `django-check` на PR №17 и на main прошёл, но Kafka-интеграция и JS в нём не выполнялись. Защита пользователя и review ментора ещё не проведены.

## Mandatory/Core M1–M9

| Код | Требование и реализация | Доказательства | Что остаётся проверить / предел |
|---|---|---|---|
| M1 | Фоновая проверка: [`schedule_weather`](../../bookings/management/commands/schedule_weather.py), [`run_weather_check`](../../bookings/weather_scheduler.py). | `SchedulerCommandTests.test_enabled_scheduler_uses_hourly_interval`, `AutomaticWeatherTests.test_clear_blocked_clear_and_error_preserves_blocked`; локально запущены два цикла настоящей команды scheduler с искусственными прогнозами, оба создали переход и Outbox. | Таймер и HTTP подменены; час реального ожидания и реальный Open-Meteo не проверялись. |
| M2 | Ухудшение: [`apply_weather_transition`](../../bookings/weather_transitions.py), [`weather-policy.md`](weather-policy.md). | `AutomaticWeatherTests.test_clear_blocked_clear_and_error_preserves_blocked`, `KafkaIntegrationTests.test_forecast_outbox_real_broker_consumer_and_redelivery`; локальный искусственный дождь → Outbox → Kafka → результат. | Синтетический прогноз не подтверждает реальный прогноз или доставку клиенту. |
| M3 | Улучшение и новый эпизод: тот же сервис переходов и [`kafka_pipeline.py`](../../bookings/kafka_pipeline.py). | `NotificationTests.test_sequential_transitions_create_two_simulations`, `test_second_episode_not_duplicate`; два цикла локального scheduler дали blocked → clear. | Устаревшее предупреждение может быть подавлено; автоматическое восстановление отменённой брони отсутствует. |
| M4 | Kafka: [`compose.yaml`](../../compose.yaml), publisher / consumer в [`kafka_pipeline.py`](../../bookings/kafka_pipeline.py). | Два теста `KafkaIntegrationTests` выполнены с настоящим брокером; проверены topic, одна partition и доступ с localhost. | Один broker и диск не дают высокой доступности; новый GitHub workflow ещё не выполнен. |
| M5 | Контракт и причины: [kafka-design.md](kafka-design.md), [decision-log.md](decision-log.md), [system-sequences.md](system-sequences.md). | Типы событий, key, version, event_id, Outbox и Consumer зафиксированы; `test_episodes_no_duplicate_and_message_identity` проверяет контракт. | Ранние самостоятельные формулировки пользователя восстановлены как реконструкция, не дословная цитата; объяснение владельца проверяется на Defense. |
| M6 | Имитация: [`SimulatedNotification`](../../bookings/models.py), [`process_notification`](../../bookings/kafka_pipeline.py), история в [`api.py`](../../bookings/api.py). | Kafka integration test и `QueueAPITests.test_notification_history_distinguishes_result_states`; в браузере показаны simulated, suppressed и отсутствие результата. | SMS/email/звонок не выполняются; имитация не подтверждает контакт. |
| M7 | Level 3 Track B: [system-sequences.md](system-sequences.md), [kafka-design.md](kafka-design.md); функции scheduler/publisher/consumer в коде. | Связь компонентов с реальными функциями и проверками задокументирована. | **Открыто:** личное объяснение пользователя и Defense ещё не проходили; автоматический тест не доказывает понимание. |
| M8 | Retry Open-Meteo: [`fetch_automatic_forecast`](../../bookings/weather_client.py), [reliability-decisions.md](reliability-decisions.md). | `test_timeout_retry_then_success_and_pauses`, `test_503_retries_and_exhausts`, `test_429_long_retry_after_is_persisted_and_next_run_deferred`, `test_invalid_and_permanent_errors_do_not_retry`. | Все ответы контролируемые; реальная доступность Open-Meteo не проверялась. |
| M9 | Transactional Outbox: [`apply_weather_transition`](../../bookings/weather_transitions.py), [`publish_pending`](../../bookings/kafka_pipeline.py), [kafka-design.md](kafka-design.md). | `test_atomic_rollback_on_history_and_outbox_failure`, `test_ack_then_database_failure_replays_same_event_id`, реальная повторная доставка и один бизнес-результат. | Нет единой транзакции PostgreSQL + Kafka и нет exactly-once; личное объяснение dual-write остаётся на Defense. |

Имена тестов относятся к [`test_weather_scheduler.py`](../../bookings/tests/test_weather_scheduler.py), [`test_kafka_pipeline.py`](../../bookings/tests/test_kafka_pipeline.py), [`test_weather_outbox.py`](../../bookings/tests/test_weather_outbox.py) и [`test_operator_queue.py`](../../bookings/tests/test_operator_queue.py). Теоретические пункты M5/M7/M9 не объявляются лично освоенными на основании кода.

## Фактические результаты

| Проверка | Окружение и результат | Ограничение |
|---|---|---|
| Полный Django-набор | Windows Python 3.14.8, отдельная PostgreSQL 18.6 и Kafka 4.2.2 на localhost: **215 passed, 0 failed, 0 skipped**, exit 0; 160,371 с тесты, 168,47 с команда. Оба `KafkaIntegrationTests` — `ok`. | Open-Meteo подменён; Kafka содержит только синтетические сообщения. |
| Повтор по README | В отдельном Compose-проекте `kazan-stage06-readme` выполнена та же последовательность команд README с заменой имени проекта: **215 passed, 0 failed, 0 skipped**, exit 0; 52,093 с. После прогона `test_kazan_courts` отсутствует, development DB существует и CONNECT для test role запрещён (`f|t|f`). `makemigrations --check --dry-run`: изменений нет. | Два Kafka-теста выполнялись через `KAFKA_INTEGRATION=1`; раздельная проверка их имён — в прогоне с `--verbosity 2` выше. |
| JavaScript | Node 24.13.0, `node --test bookings/tests/operator_queue_ui.test.js`: **4 passed, 0 failed, 0 skipped**. | Это тесты поведения с поддельным DOM; отдельный браузерный сценарий этапа 5 выполнялся на синтетических данных. |
| Проверка Kafka с host | `KafkaConsumer` подключён к `127.0.0.1:9092`; `booking.weather.v1` имеет `{0}`. | Это локальная аналогия сети GitHub runner, не запуск Actions. |
| Compose | `docker compose -p kazan-stage06 up -d --build --wait --wait-timeout 180`; миграции, `seed_demo_data`, `migrate --check`, `check`, `pip check`: PASS; Django `kazan_courts_dev/kazan_dev`, `/login/` HTTP 200. | Отдельные синтетические volumes; `up --wait` без web healthcheck не гарантирует готовность HTTP. |
| Два запуска scheduler | В отдельном Compose-проекте запущена настоящая management-команда с подменённым прогнозом и sleep: 2 цикла, 2 события и 2 Outbox; после обработки blocked suppressed, cleared simulated. | Не использован реальный таймер часа или сетевой Open-Meteo; между циклами Consumer не обязан обработать первое событие. |
| Сохранность Compose | Обычный `down` и `up`: бронь clear, 2 Outbox и 2 результата Consumer сохранены. | Проверка не заменяет backup. |
| Изоляция PostgreSQL | После тестов `test_kazan_courts` отсутствует; Compose development DB существует; `kazan_test` не имеет CONNECT к ней (`f|t|f`). | Не относится к пользовательской production-базе. |
| YAML и контроль пропусков | YAML разобран локально; синтетические журналы: два `ok` принимаются, пропуск и отсутствие тестов отклоняются. | Это проверка логики, а не выполнение workflow в GitHub. |
| GitHub CI этапа 6 | **NOT VERIFIED** — новый workflow нельзя запускать без согласованных commit/push/PR. | Лимит увеличен с 10 до 15 минут для старта двух сервисов, установки зависимостей и полного набора (168,47 с на локальном host); фактическое время GitHub runner ещё не измерено. |

## Воспроизведение

Полные команды создания **отдельной** среды `kazan-stage04-test`, ожидания готовности db и Kafka, сборки и прогона `KAFKA_INTEGRATION=1` приведены в [README: изолированная интеграционная проверка Kafka](../../README.md). Для повтора этапа 6 во всех командах имя `kazan-stage04-test` заменено на новое `kazan-stage06-readme`; все прочие команды сохранены. Этот проект создаёт отдельные volumes; не используйте рабочий broker для тестовых сообщений. Команда внутри среды:

```powershell
docker compose -p kazan-stage04-test run --rm --no-deps -e DB_NAME=postgres -e DB_USER=kazan_test -e DB_PASSWORD -e KAFKA_INTEGRATION=1 web python manage.py test test_database bookings --verbosity 2 --noinput
node --test bookings/tests/operator_queue_ui.test.js
```

`DB_PASSWORD` передаётся из заранее подготовленного окружения, как в README; не выводите реальные credentials. Node 24.13.0 не требует стороннего тестового фреймворка. Запуск с `KAFKA_INTEGRATION` отсутствующим пропускает два Kafka-теста. Новая CI-конфигурация отдельно проверяет строки `... ok` для обоих тестов и заваливает job, если они отсутствуют.

Для локального прогона этапа 6 использовался проектный `.venv` и два отдельных контейнера: PostgreSQL на `127.0.0.1:15436` под `kazan_test`, Kafka на `127.0.0.1:9092`, `KAFKA_INTEGRATION=1`, `WEATHER_NETWORK_ENABLED=0`; точная команда: `.venv\Scripts\python.exe manage.py test test_database bookings --verbosity 2 --noinput`. Пароли и ключ были синтетическими и заданы только процессу. Тесты создают и удаляют `test_kazan_courts`.

## Advisory lock: прежний сбой и исследование

На этапе 5 итоговый прогон один раз завершился ошибкой `test_parallel_lock_and_release`: в `test_weather_scheduler.py`, строка 195, `self.assertTrue(cursor.fetchone()[0])`, `AssertionError: False is not true`. После этого адресный тест и полный повтор 215/215 прошли; сбой не удалён из истории отчёта.

На этапе 6 отдельный эксперимент с двумя настоящими PostgreSQL-сессиями выполнил 200 циклов: владелец брал lock, закрывал клиентский сокет, другая сессия немедленно пробовала захватить тот же lock. В **5/200** попытках lock ещё удерживался; максимум наблюдаемого ожидания до освобождения — **0,0452 секунды**. Это воспроизводит механизм гонки, совместимый с прежней ошибкой; точное состояние сервера в момент прежнего сбоя не записывалось, поэтому его нельзя доказать задним числом.

Тест теперь ждёт до пяти секунд фактического исчезновения advisory lock закрытой сессии в `pg_locks`, затем по-прежнему проверяет успешный захват и освобождение. Повтор исправленного теста: **20 passed, 0 failed, 0 skipped**. Тест не изменяет runtime-код scheduler.

## Отказ, повторы и ограничения

- На этапе 4 реальная Kafka останавливалась: Outbox оставался неопубликованным с `kafka_unavailable`; после восстановления запись была опубликована и обработана. В текущем полном наборе ошибки отправки и сбой после подтверждения broker также проверяются через подмену producer; повтор Kafka после DB commit до offset commit — с настоящим broker.
- Некорректное сообщение повторно доставляется и задерживает последующие события единственной partition. DLQ и автоматического пропуска нет.
- `Retry-After` до семи суток соблюдается, более длинное значение ограничивается семью сутками с предупреждением; это может означать повтор раньше указанного провайдером срока.
- Реальные вызовы Open-Meteo и клиентские уведомления в этих проверках не выполнялись. `last_success_at` подтверждает получение прогноза, не завершение обработки всех броней.
- Mandatory/Core не считается пройденным пользователем до отдельного Defense, а Homework 004 — принятым ментором до его review.
