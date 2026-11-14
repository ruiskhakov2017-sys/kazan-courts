# Локальная traceability KazanCourts — этап 1

Центральная матрица обоих приложений остаётся вне этого изменения. Реестр M/Q/U и статусы теории/защиты не закрываются.

| Требование и источник | Реализация | Проверка | Доказательство |
|---|---|---|---|
| P01, Homework §5, roadmap A01–A04/A11 | compose.yaml, Dockerfile, docker/postgres/Dockerfile | fresh volumes → migrations → все сервисы, retained-volume restart | testing-report, logs 28/32 |
| P02, §5, A09 | .env.example, scripts/init_local_env.py, config/settings.py | port18005, DEBUG/hosts overrides, no overwrite | testing-report |
| P03, §5, A10 | .gitignore/.dockerignore, explicit COPY, private .env | diff/files/logs/image config+history+layers; known generated secrets and common token signatures | scripts/audit_local_secrets.py, testing-report |
| P04, §5.1, C07 | config/health.py live, Compose healthcheck | live 200 при DB/Kafka stop | config/test_health.py, scripts/check_production.py |
| P05, §5.1, C07 | config/health.py ready, config/urls.py | ready200 →503 →200, оба отказа | config/test_health.py, scripts/check_production.py |
| P06–P13 | Вне разрешённого этапа | Not Run | production-practice, operations, backup-restore |

[Оригинал Homework](sources/Mentor_Homework_005.md) и [roadmap](sources/ai_native_engineering_roadmap.md) сохранены из присланного архива без редакторских изменений.
Итоговая защита и независимый Review ещё не проведены.
