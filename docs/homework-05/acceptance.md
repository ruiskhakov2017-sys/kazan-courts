# KazanCourts P01–P13 — итоговая техническая проверка

21 ноября 2026. **P01–P13 реализованы. В выполненных проверках ошибок не обнаружено.** Это ограниченный результат тестирования, а не гарантия отсутствия всех возможных ошибок или автоматическая приёмка ментора/CP5.

## GitHub Actions: фактически подтверждено

- Реализация: `05c4d2f283e9f136d44119d086e16708203ae9b1`.
- [PR №20](https://github.com/ruiskhakov2017-sys/kazan-courts/pull/20).
- [Полный hosted run 44939563350 — PASS](https://github.com/ruiskhakov2017-sys/kazan-courts/actions/runs/44939563350).
- GitHub-hosted `ubuntu-24.04`, без self-hosted/ASUS/MSI.
- Artifact `kazancourts-p01-p13-44939563350-1` скачан и проверен по содержимому, не только по зелёному статусу.

| Проверка | Результат |
|---|---|
| Холодная сборка, весь Compose, миграции/модели, monitoring | PASS |
| PostgreSQL/Kafka stop/recovery, liveness/readiness, сохранение данных | PASS |
| Django | 228 обнаружено/выполнено; 0 failures/errors/skips/expected failures/unexpected successes |
| JavaScript | 4 tests / 4 pass, 0 skipped/cancelled/todo |
| Safety gates CI | 14 tests, OK; отрицательные случаи проверены |
| Structured logs и durable correlation | PASS: HTTP/Outbox/Kafka/consumer |
| Сквозной trace | PASS: все 5 parent-linked этапов с длительностями |
| Metrics и dashboard backend | PASS: success/error counters, duration, JSON/Prometheus/HTML |
| PostgreSQL backup/restore | PASS: custom pg_dump18.6, чистая другая БД; public rows/sequences fingerprints совпали |
| Восстановление работы | PASS: unfinished acknowledged event requeued; notification завершена без дубликата; HTTP read/write201 |
| Обновление приложения/схемы | PASS: исходное приложение P01–P05/0006 → новая версия/0007, бронь сохранена, новая запись201, ready200 |
| Временные секреты и cleanup | PASS в документированном scope; env/raw/dump и новые ресурсы удалены |

Фактическая hosted-трасса: `bbb9d5c3ddfe458ca554dbf6e90be9f3`.
Hosted dump: 53800 bytes, SHA256 `f4d09ccb141821cd9effa1c3bcc690112f5d37366c4ef7f704a80e5826e84cb6`.
Сам dump и credentials в Git/публичный artifact не входят.
Последующие правки этого протокола не меняют runtime-код; актуальные проверки конечного head и merge доступны в PR и [workflow CI](https://github.com/ruiskhakov2017-sys/kazan-courts/actions/workflows/ci.yml).

## Codex

Ранее подтверждены 228 Django, 4 JS, 14 safety tests, фактические backup/restore/update и тот же полный pipeline. Dashboard дополнительно проверен настоящим Chromium: 19 строк метрик, все этапы трассы отображаются, JavaScript errors=[]; screenshot находится в опубликованном пакете Review.

## Границы и эксплуатация

Merge разрешён пользователем только после успешных проверок. Deployment и изменение действующих данных не выполняются. Старые volumes не удалялись и не переносились; все destructive проверки выполнялись на собственных новых synthetic ресурсах.
[Матрица](production-practice.md), [observability](observability.md), [backup/restore](backup-restore.md), [runbook](operations.md) и исторические результаты [testing-report](testing-report.md) содержат точные границы. Датированные Not Run/без commit в старых снимках описывают их состояние до первого hosted run.
Нет утверждения о zero-downtime, автоматическом rollback данных, внешней погоде, HA/security certification или завершении защиты пользователя. Telemetry best-effort; upstream Kafka generic-key audit имеет указанную границу, поиск временных CI-секретов сохраняется во всех образах. VidHarbor не изменялся.
