# System sequences — реализация Homework 004

## Implemented: исходная цепочка

Исходная цепочка: браузер → `bookings.api.create()` → `services.create_booking()` → `weather_client.fetch_forecast()` для открытого корта → `_create_booking()` → PostgreSQL (`Booking` + `BookingEvent` в одной транзакции). Ручная перепроверка вызывается `api.weather_recheck()` → `services.recheck_weather()`; теперь её надёжные погодные переходы тоже атомарно записывают системную историю и Outbox наряду с действием сотрудника. Ниже — фоновая цепочка этапа 4, объединённая в main; её проверка описана в [testing-report.md](testing-report.md).

## Реализация этапа 4: компоненты

```mermaid
flowchart LR
  Scheduler --> Weather[Open-Meteo adapter]
  Scheduler --> Transition[Booking transition service]
  Transition --> PG[(PostgreSQL: Booking, BookingEvent, Outbox)]
  Publisher --> PG
  Publisher --> Kafka[Kafka: один topic]
  Kafka --> Consumer
  Consumer --> PG2[(PostgreSQL: SimulatedNotification)]
  UI[Экран сотрудника] --> PG
```

## Реализация этапа 4: ухудшение / улучшение

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant W as Open-Meteo
    participant D as PostgreSQL
    participant P as Publisher
    participant K as Kafka
    participant C as Consumer
    S->>W: Получить прогноз вне транзакции
    W-->>S: Результат или ошибка
    S->>D: atomic: перепроверка брони + переход + BookingEvent + Outbox
    D-->>S: commit / rollback
    P->>D: Взять ожидающее событие
    P->>K: event_id, booking_id, тип
    K-->>P: Подтверждение
    P->>D: Отметить опубликованным
    K->>C: Событие, возможен повтор
    C->>D: atomic: актуальность + уникальная имитация
    D-->>C: commit
    C->>K: Подтвердить offset
```

При сбое погоды нет очищающего события; время последнего автоматического успеха и техническое состояние фиксируются отдельно. При повторе используется тот же `event_id`. Функции цепочки: `weather_scheduler.run_weather_check`, `weather_transitions.apply_weather_transition`, `kafka_pipeline.publish_pending` и `kafka_pipeline.process_notification` / `consume_one`. Контракт, ограничения порядка и сбои — [kafka-design.md](kafka-design.md).
