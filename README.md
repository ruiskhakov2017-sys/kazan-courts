# KazanCourts

Учебный сервис бронирования теннисных кортов. Сейчас доступна только стартовая страница. Требования и архитектура находятся в docs/.

## Локальный запуск (PowerShell)

Нужен Python 3.14.8. Все команды выполняются из корня репозитория.

    py -3.14 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    $env:DJANGO_SECRET_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    .\.venv\Scripts\python.exe manage.py check
    .\.venv\Scripts\python.exe manage.py runserver --noreload 127.0.0.1:8000

Откройте http://127.0.0.1:8000/ в браузере. Для остановки сервера нажмите Ctrl+C. Значение DJANGO_SECRET_KEY действует только в текущем сеансе PowerShell; не сохраняйте его в Git.

На этом этапе база данных не подключена: миграции не запускайте. PostgreSQL и Docker Compose добавляются в задаче 02.
