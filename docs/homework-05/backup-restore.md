# P11/P12 — PostgreSQL backup и фактический restore

21 ноября 2026, KazanCourts, отдельный новый CI-проект. Рабочие БД/volumes и VidHarbor не использовались.

## Что проверялось

1. Учебный PostgreSQL18.6: schema/migrations, synthetic courts/customers/employee, booking/events, Outbox и persisted notification. В БД оставлено одно событие с broker ack без notification для проверки восстановления незавершённой работы.
2. После остановки всех writers сделан настоящий `pg_dump -Fc --no-owner --no-privileges` под kazan_dev. Формат PGDMP, SHA256/размер и фактические версии — `full-backup-restore.json`, `full-dump-version.log`, `full-server-version.log` в Review evidence.
3. Подняты отдельный restore-db с новым volume и новый restore-kafka. Перед pg_restore подтверждено **0 public tables**, исходная БД не перезаписывалась.
4. Выполнен `pg_restore --exit-on-error --no-owner --no-privileges`. Сравниваются SHA256 полного отсортированного содержимого всех public tables и `last_value/is_called` всех public sequences, счётчики таблиц. Совпадение проверяется **до** reconciliation/новых записей. Применённая миграционная история и `migrate --check` подтверждают версию схемы; ошибок восстановления constraints/indexes не допускается.
5. В restore-db выполнен guarded reconcile Outbox; новые workers завершают именно утраченную незавершённую работу в новом Kafka. Повтор reconcile не создаёт новую notification.
6. Восстановленный web отвечает readiness200; реальный HTTP с синтетической authenticated session читает контрольную бронь и создаёт новую indoor booking+history (201). Это также проверяет восстановленные sequences/связи.

## Сохранение и приватность

Сам dump — **приватный файл**, не в Git и не в публичном ZIP. Локальная копия для независимого Review находится в `/workspace/hw05-full-review/private-backups/` с каталогом0700 и файлами0600. Это только synthetic данные; session/auth значения всё равно не публикуются. В CI временный dump удаляется после проверки. SHA/версия/контрольные результаты публикуются отдельно без строк клиентских/секретных данных.
Доказательства исполняет `scripts/check_full_production.py`; точный итог PASS/FAIL и конкретные filenames — в testing-report. Наличие только команды или dump не объявляется успешным restore.

## Границы

Эта практика не измеряет production RPO/RTO и не переносит существующий Debian-volume в Alpine. Данные приложения восстановлены под соответствующей версией новой схемы; бизнес-файлов в KazanCourts нет. Dump не включает .env, role passwords, Docker images, Kafka logs/offsets и diagnostic telemetry. Источники этих частей и политика нового broker описаны в [operations](operations.md).
Отказ реального погодного провайдера, внешние сообщения, production disaster recovery и восстановление исторических trace/counter данных не заявляются.
