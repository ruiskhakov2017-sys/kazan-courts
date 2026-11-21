# KazanCourts P06–P13 — реализация для Review

[Скачать полный ZIP](KazanCourts_HW005_P06-P13_Review.zip?raw=true)

В Codex 21 ноября прошли 228 Django tests (без пропусков), 4 JS tests, 14 CI safety tests.
Проверены настоящая HTTP→Outbox→Kafka→consumer трасса с длительностями, JSON logs/correlation,
живые metrics/dashboard (Chromium screenshot), реальный pg_dump18.6/clean restore,
возобновление незавершённой работы, API read/write и обновление версии/схемы0006→0007.

Код находится в ZIP и full.diff, в незакоммиченной рабочей ветке feature/hw05-kazancourts-p06-p13.
Эта отчётная ветка содержит только материалы Review: новая реализация не отправлена в main/PR.
Новая версия GitHub Actions — NOT RUN, независимый Review ещё не проведён.
Private dump и .env намеренно отсутствуют в публичном пакете. Их заменяют metadata/hash и фактические результаты восстановления.
Действующие данные/volumes и VidHarbor не изменялись. Внешний deployment не выполнялся.
