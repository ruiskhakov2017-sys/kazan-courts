# KazanCourts: повторный Review P01–P05, CI и PostgreSQL

14 ноября 2026.

- [Скачать полный ZIP](KazanCourts_HW005_P01-P05_CI_Review.zip?raw=true)
- [Отчёт повторного Review](ci-review.md)
- [Отчёт проверок](testing-report.md)
- [Риск PostgreSQL volumes](postgresql-volume-review.md)
- [Diff новых изменений](review-changes.diff), [полный diff](full.diff)
- [Безопасные результаты CI-оркестратора в Codex](ci-results/)

В Codex: 221 Django + 4 JS + 12 CI gate tests, проверки отказов/восстановления, временных секретов и cleanup прошли. GitHub Actions для новой версии workflow **Not Run**: код приложения и workflow остаются незакоммиченными в изолированной feature-ветке. В этой ветке опубликованы только материалы Review, архив и diff. Main не изменён.
