FROM python:3.14.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 app

COPY --chown=app:app manage.py test_database.py ./
COPY --chown=app:app config ./config
COPY --chown=app:app bookings ./bookings
COPY --chown=app:app templates ./templates

USER app

CMD ["python", "manage.py", "runserver", "--noreload", "0.0.0.0:8000"]
