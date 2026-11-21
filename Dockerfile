FROM python:3.14.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN --mount=type=secret,id=cloud_ca,target=/run/cloud-ca.pem \
    if [ -f /run/cloud-ca.pem ]; then export PIP_CERT=/run/cloud-ca.pem; fi; \
    python -m pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 app \
    && install -d -o app -g app /var/lib/kazan-observability

COPY --chmod=0755 --chown=app:app manage.py test_database.py ./
COPY --chmod=0755 --chown=app:app config ./config
COPY --chmod=0755 --chown=app:app bookings ./bookings
COPY --chmod=0755 --chown=app:app templates ./templates
COPY --chmod=0755 --chown=app:app static ./static

COPY --chmod=0755 --chown=app:app scripts ./scripts

USER app

CMD ["python", "manage.py", "runserver", "--noreload", "0.0.0.0:8000"]
