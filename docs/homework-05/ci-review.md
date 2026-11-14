# Повторный Review P01–P05: облачный CI и PostgreSQL

Дата: 14 ноября 2026. Работа в прежнем worktree и ветке `feature/hw05-kazancourts-production`.
Исходные незакоммиченные изменения сохранены; перед работой создана отдельная копия исходников без `.env`.
По сообщению пользователя первоначальный независимый Review реализации пройден с двумя открытыми вопросами. Этот документ отвечает на них; повторная независимая приёмка ещё не проведена.

## Изменения

- Существующий job `django-check` теперь проверяет фактический Compose вместо другого стенда из Actions services. Используется только GitHub-hosted `ubuntu-24.04`. Оригинальные закреплённые checkout/setup-node сохранены; upload-artifact также закреплён SHA.
- `prepare` отказывается использовать существующие project resources/images/state. Создаёт отдельный приватный env-файл, не трогает локальный `.env`; secrets не записываются в GitHub variables или вывод команд.
- `run`: build `--pull --no-cache`, up/wait, проверка каждого сервиса и exit0 миграций, Django check/схема, реальные DB/Kafka stop/recover, down/up с сохранением тестовых данных, затем остановка workers для тестов Kafka, полный Django и JS наборы.
- Новый `config.ci_runner.CIResultRunner` сохраняет JSON с обнаруженными test IDs, фактическим числом выполнений и всеми исходами. Проверка требует не менее 221 теста, соответствия discovery/execution, присутствия обоих real Kafka tests, отсутствия failures/errors/skips/expected failures/unexpected successes. При добавлении тестов число берётся из discovery и JSON. Для TAP JavaScript требуется не менее 4 тестов, все pass, без skipped/cancelled/todo.
- `always()` выполняет аудит временных секретов, остановку и очистку только этого нового project. Проверяет отсутствие его containers/networks/volumes/local build images. Приватный env и raw-логи удаляются. Сторонние volumes/images не удаляются.
- Только аудированные логи/JSON попадают в artifact. В случае утечки или ошибки аудита/cleanup подробные данные не выгружаются, остаётся фиксированный summary FAIL. Exit status тестовой фазы не подменяется успешным cleanup. Сводка workflow учитывает outcome всех фаз.
- Alpine PostgreSQL использует отдельный volume key; [минимальное решение и риск](postgresql-volume-review.md). Старые volumes не подключались и не удалялись.

## Политика проверки секретов

Все четыре временных значения проверяются без маскирования в исходных raw-результатах, исходниках/diff и config/history/**всех слоях всех образов** этого проекта, включая неизменённый официальный Kafka.
Проверка идёт до публикации результатов. Если найдено значение, прогон FAIL, даже если последующая redaction удаляет его из безопасной сводки.

Для собственного Django/DB образов, кода и логов также выполняется поиск распространённых credential/key-сигнатур. Официальный неизменённый `apache/kafka:4.2.2` содержит встроенный PEM-материал в `libgnutls.so`; это отдельно воспроизведено аудитом upstream image. Для этого образа применяется явный режим `--temporary-secrets-only-image`: он **сохраняет полный поиск генерируемых CI-значений**, но не заявляет отсутствие всех upstream test-key fixtures. Образ Kafka и механизмы Kafka не изменены. Это ограничение проверки, а не скрытый PASS общего security audit.

Негативные проверки подтверждают: значения временного секрета даже в таком upstream image дают FAIL; leaked raw-лог не попадает в artifact; отсутствие owner marker запрещает cleanup; пропущенные/нулевые/неполные тесты и отсутствие Kafka tests не проходят.

## Проверено в Codex

Статусы окончательного прогона и точные числа находятся в приложенных безопасных результатах. GitHub YAML проверен `actionlint` 1.7.7 (официальный архив проверен по SHA256). Контракты конфигурации проверяют GitHub-hosted runner и раздельные volumes. Python helper syntax и отрицательные проверки результата выполнены локально в облаке.

Два запуска оркестратора разделены: первый выполнил Compose/221 Django/4 JS, но корректно завершился **FAIL** на общей сигнатуре в upstream Kafka; cleanup прошёл, подробные raw-логи не выгружены. После уточнения явного scope upstream image выполнен новый полный прогон на свежем project. Его финальный статус фиксируется в [testing-report](testing-report.md).
Codex-прогон использует только дополнительный build-secret CA для облачного прокси. GitHub-hosted runner не нуждается в этом cloud override. Прямое исполнение Python оркестратора в Codex не является запуском GitHub Actions.

## Проверено в GitHub Actions

**Not Run для этой версии изменений.** Нет run URL/commit с данным workflow, artifact или подтверждённого GitHub-hosted результата. Commit/push/PR/merge/deployment и workflow_dispatch в этой задаче не выполнялись и не разрешены.

После отдельного разрешения потребуется опубликовать изменения в feature-ветке и проверить настоящий Actions run: SHA, все phase outcomes, JSON counts, отсутствие skips, cleanup и скачанный artifact. До этого готовность именно GitHub execution environment остаётся открытой.

## Пока не проверено

Фактическое исполнение workflow на GitHub-hosted runner, загрузка artifact сервисом GitHub, job cancellation/host failure cleanup. Работа со старыми Debian volumes, физическая совместимость и перенос рабочих данных не проверялись; in-place переход не предлагается.
P06–P13 и полный production readiness не начаты. Изменений booking rules/weather/Kafka processing нет.
