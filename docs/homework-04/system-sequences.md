# System sequences — Draft

## Implemented: исходная цепочка

Текущие имена и границы подтверждены в [current-system-map.md](current-system-map.md): браузер → `bookings.api.create()` → `services.create_booking()` → `weather_client.fetch_forecast()` для открытого корта → `_create_booking()` → PostgreSQL (`Booking` + `BookingEvent` в одной транзакции). Ручная перепроверка вызывается `api.weather_recheck()` → `services.recheck_weather()`. Фоновой Kafka-цепочки в текущем коде нет.

## Planned: компоненты

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

## Planned: ухудшение / улучшение

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

При сбое погоды нет очищающего события; время последнего автоматического успеха и техническое состояние фиксируются отдельно. При повторе используется тот же `event_id`. Контракт, ограничения порядка и сбои — [kafka-design.md](kafka-design.md); будущие пути файлов/функций уточняются по фактической реализации этапа 4.
