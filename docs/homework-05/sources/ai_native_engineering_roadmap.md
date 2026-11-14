# AI-Native Engineering Roadmap

## Goal

Перейти от **AI-assisted / vibe coding**, где агент в значительной степени сам выбирает архитектуру и технологии, к состоянию, где менти:

- понимает назначение основных компонентов современной системы;
- может оценивать предложения AI и задавать ему правильные ограничения;
- самостоятельно принимает ключевые архитектурные решения;
- умеет объяснить trade-offs и отказаться от ненужной сложности;
- использует AI как сильного исполнителя и консультанта, а не как единственного архитектора;
- способен спроектировать, реализовать и довести до production собственный сервис.

## Как проходить roadmap

Roadmap **не привязан к неделям или месяцам**. Уровни могут частично пересекаться.

Тема считается пройденной, когда менти может:

1. объяснить её своими словами;
2. объяснить, какую проблему она решает;
3. привести пример, когда её **не стоит** использовать;
4. применить знание в проекте;
5. защитить принятое решение на review.

Статусы:

- [ ] Not started
- [~] In progress
- [x] Done
- [R] Reviewed

---

# Level 1 — Foundation

## Track A — AI-native workflow

- [ ] AGENTS.md / CLAUDE.md / project instructions
- [ ] Skills: что это, зачем нужны, где полезны
- [ ] MCP: назначение и отличие от skills/tools
- [ ] Tools и permissions
- [ ] Subagents / delegation
- [ ] Context management
- [ ] Planning → implementation → review → fix
- [ ] Выбор модели и reasoning effort
- [ ] Scope и constraints для агента
- [ ] Git / branches / PR как safety mechanism

### Definition of Done

Менти может показать свой рабочий AI-development workflow и объяснить, зачем нужен каждый его этап.

## Track B — Engineering foundations

- [ ] Frontend / Backend / API
- [ ] Process / Service / Container
- [ ] Stateful vs Stateless
- [ ] Database vs Cache
- [ ] SQL vs NoSQL
- [ ] PostgreSQL: зачем relational database
- [ ] Redis: cache / broker / ephemeral state
- [ ] HTTP basics
- [ ] REST API basics

### System Design orientation

Ознакомительно прочитать **главы 2–3 System Design Interview**.

Цель сейчас — не запомнить детали, а увидеть цепочку:

**requirements / scale / failure mode → architectural decision**

После чтения уметь привести несколько примеров:

- много повторных чтений → возможно, cache;
- долгая операция → возможно, background job / queue;
- несколько backend instances → load balancing;
- рост нагрузки на storage → replication / partitioning как возможные направления.

## Track C — Quality basics

- [ ] Test pyramid
- [ ] Unit tests
- [ ] Integration tests
- [ ] E2E / acceptance tests
- [ ] Что именно должен доказывать тест
- [ ] Happy path vs edge cases

## Practice — Project 0: Axyro

Axyro используется как **диагностический sandbox**, а не как проект на весь roadmap.

- [ ] Создать/улучшить `AGENTS.md`
- [ ] Разобраться, зачем в Axyro PostgreSQL
- [ ] Разобраться, зачем в Axyro Redis
- [ ] Разобраться, зачем в Axyro Celery
- [ ] Посмотреть текущую структуру проекта
- [ ] Выписать компоненты, назначение которых пока непонятно
- [ ] Определить, что выглядит необходимым, а что потенциально overengineered
- [ ] Сделать небольшое изменение через plan → implement → review → fix

## CP1 — Foundation checkpoint

Готово, когда:

- AI workflow стал явным и повторяемым;
- появился понятный project context для агента;
- понятна разница Skills / MCP / Tools / Subagents;
- понятна роль PostgreSQL / Redis / Celery в Axyro;
- понятно различие DB / cache / queue;
- понятны основные уровни тестирования.

---

# Level 2 — Application Structure

## Track A — AI control

- [ ] Scope control
- [ ] Constraints
- [ ] Проверка changed files / diff
- [ ] Structured prompting
- [ ] Review strategy
- [ ] Использование разных агентов для plan / implementation / review
- [ ] Model / effort selection

## Track B — Architecture

- [ ] Separation of concerns
- [ ] Coupling / cohesion
- [ ] Dependency direction
- [ ] Layered Architecture
- [ ] Clean Architecture — идеи, а не шаблон папок
- [ ] Domain / Application / Infrastructure
- [ ] Ports & Adapters — концептуально
- [ ] Modularity
- [ ] Abstraction vs overengineering

## Track C — Domain modelling

- [ ] Domain model
- [ ] Entity / Value Object — базово
- [ ] Ubiquitous Language
- [ ] Bounded Context
- [ ] Invariants
- [ ] State machines

## Practice — завершение Project 0: Axyro

- [ ] Выделить логические области / bounded contexts
- [ ] Найти места с плохой cohesion / сильным coupling
- [ ] Объяснить, почему `models.py` и крупные task-файлы устроены именно так
- [ ] Подготовить design proposal: что оставить / разделить / упростить
- [ ] **Не рефакторить автоматически только ради Clean Architecture**

## CP2 — Structure checkpoint

Готово, когда менти может:

- объяснить структуру Axyro без помощи AI;
- показать основные boundaries;
- аргументированно предложить упрощение;
- отличить необходимую abstraction от случайной сложности;
- сначала предложить design, а потом отдавать implementation агенту.

**После CP2 Axyro перестаёт быть основным учебным проектом.**

---

# Level 3 — Data & Async

## Track A — Data

- [ ] Schema design
- [ ] Primary / Foreign keys
- [ ] Constraints
- [ ] Indexes
- [ ] Transactions
- [ ] ACID
- [ ] Isolation levels — базово
- [ ] Optimistic / pessimistic locking
- [ ] Migrations
- [ ] Source of truth

## Track B — Background processing

- [ ] Sync vs async work
- [ ] Queue
- [ ] Broker
- [ ] Worker
- [ ] Scheduler
- [ ] Retry / backoff
- [ ] Dead-letter queue
- [ ] Idempotency
- [ ] At-most-once / at-least-once / exactly-once — концептуально

## Track C — Testing

- [ ] Database integration tests
- [ ] Queue/worker integration tests
- [ ] API-level tests
- [ ] Failure scenarios
- [ ] Regression scenarios

## Practice — Project 1: Structured backend service

Новый проект. Не CRUD ради CRUD — должна быть настоящая бизнес-логика.

Минимальные требования:

- API;
- PostgreSQL;
- domain rules / invariants;
- tests;
- Docker;
- понятная структура;
- **без Redis/queue, если он не сможет доказать необходимость**.

До реализации:

1. Requirements
2. Constraints
3. Architecture
4. Data model
5. API contract
6. Failure cases

### Learning trap

Если менти автоматически предлагает Redis/Celery/Kafka, он должен объяснить:

> Какую конкретную проблему это решает прямо сейчас?

## CP3 — Data checkpoint

Готово, когда:

- схема данных проектируется осознанно;
- понятны transaction boundaries;
- понятны indexes/constraints;
- менти способен аргументировать необходимость или отсутствие async processing;
- integration tests проверяют реальные сценарии системы.

---

# Level 4 — Async, Integrations & Distributed Systems

## Track A — External integrations

- [ ] OAuth 2.0
- [ ] API keys / tokens
- [ ] Webhooks
- [ ] Polling
- [ ] Rate limits
- [ ] Timeouts
- [ ] Retries
- [ ] Circuit breaker
- [ ] API versioning
- [ ] Idempotency keys

## Track B — Distributed systems thinking

- [ ] Partial failures
- [ ] Eventual consistency
- [ ] Race conditions
- [ ] Concurrency
- [ ] Duplicate requests/events
- [ ] Distributed transactions — проблема
- [ ] Transactional outbox
- [ ] Saga — концептуально
- [ ] Graceful degradation

## Practice — Project 2: Async processing / integration system

Новый проект, где async действительно нужен.

Примеры класса задач:

- document/media processing pipeline;
- bulk import + processing;
- scheduled synchronization with external API;
- multi-stage data enrichment.

Обязательные свойства:

- queue + workers;
- retries/backoff;
- idempotency;
- scheduling;
- external API;
- observable job states;
- recovery after failure.

Во время проекта ментор добавляет constraints:

- worker падает после side effect;
- событие приходит дважды;
- API отвечает 30 секунд;
- rate limit;
- часть операции выполнилась;
- несколько workers работают конкурентно.

## CP4 — Distributed checkpoint

Готово, когда менти:

- ожидает partial failures по умолчанию;
- понимает delivery guarantees;
- проектирует idempotency;
- может описать failure modes;
- может объяснить consistency model;
- умеет адаптировать существующую архитектуру при изменении требований.

---

# Level 5 — Production Engineering

## Track A — Deployment

- [ ] Docker image / container
- [ ] Volumes
- [ ] Networking
- [ ] Docker Compose
- [ ] VPS vs PaaS vs Cloud
- [ ] Reverse proxy
- [ ] DNS
- [ ] TLS / HTTPS
- [ ] Environments
- [ ] Secrets
- [ ] Database migrations during deployment
- [ ] Rollback

## Track B — CI/CD

- [ ] Build
- [ ] Lint / static checks
- [ ] Tests
- [ ] Artifacts
- [ ] Deployment
- [ ] Rollback strategy

## Track C — Observability & Reliability

- [ ] Structured logs
- [ ] Metrics
- [ ] Traces
- [ ] Correlation IDs
- [ ] Dashboards
- [ ] Alerts
- [ ] Health / readiness
- [ ] Backups
- [ ] Restore
- [ ] Graceful shutdown
- [ ] RPO / RTO — базово
- [ ] SLI / SLO — базово

## Track D — Security foundations

- [ ] Authentication vs Authorization
- [ ] Sessions / JWT
- [ ] RBAC
- [ ] Secrets
- [ ] Encryption in transit / at rest
- [ ] Input validation
- [ ] SQL Injection
- [ ] XSS
- [ ] CSRF
- [ ] SSRF
- [ ] Least privilege
- [ ] Threat modelling
- [ ] Audit logs
- [ ] Dependency vulnerabilities

## Practice — Productionize Project 1 or Project 2

Не создавать новый проект ради infrastructure.

Выбрать один предыдущий сервис и добавить:

- CI/CD;
- deployment;
- observability;
- alerting;
- secrets;
- backups;
- security review;
- load/failure testing;
- recovery procedure.

## CP5 — Production checkpoint

Готово, когда менти способен:

- развернуть сервис без «магии» агента;
- объяснить production topology;
- диагностировать сбой по telemetry;
- выполнить rollback/recovery;
- объяснить основные security boundaries.

---

# Level 6 — AI Engineering

## Track A — LLM application fundamentals

- [ ] LLM API
- [ ] Tokens / context
- [ ] Structured outputs
- [ ] Tool / function calling
- [ ] Streaming
- [ ] Retry / fallback
- [ ] Model routing
- [ ] Cost / latency

## Track B — AI systems

- [ ] RAG
- [ ] Embeddings
- [ ] Vector search / vector databases
- [ ] Memory
- [ ] Human-in-the-loop
- [ ] Guardrails
- [ ] Agent loops
- [ ] MCP servers
- [ ] Multi-agent architectures
- [ ] Deterministic vs probabilistic boundaries

## Track C — Evals

- [ ] Что считать quality
- [ ] Eval dataset
- [ ] Baseline
- [ ] Failure taxonomy
- [ ] Deterministic checks
- [ ] LLM-as-judge
- [ ] Regression
- [ ] Model comparison
- [ ] Cost / latency evaluation

## Practice — Project 3: Production AI service

Новый сервис, где AI решает реальную задачу.

Условие:

**не использовать RAG, agents, vector DB, memory или multi-agent только потому, что они есть в roadmap.**

Для каждого компонента нужно объяснить необходимость.

На выходе:

- architecture;
- deterministic boundaries;
- AI feature;
- eval dataset;
- baseline;
- metrics;
- production deployment;
- observability;
- cost/latency understanding.

## CP6 — AI Engineering checkpoint

Готово, когда:

- AI quality измеряется, а не оценивается «на глаз»;
- менти умеет выбирать между обычным кодом и LLM;
- понимает риски tool use / prompt injection;
- способен аргументировать архитектуру AI-system.

---

# Level 7 — Independent Engineering

Здесь ментор больше не задаёт архитектуру или стек.

## Final Project — Own Product

Менти самостоятельно выбирает продукт/проблему.

Процесс:

1. Idea / Problem
2. Requirements
3. Non-functional requirements
4. Constraints
5. Architecture proposal
6. Trade-offs
7. Data model
8. APIs / integrations
9. Failure model
10. Security model
11. Implementation plan
12. Implementation with AI
13. Testing
14. Deployment
15. Observability
16. Production feedback
17. Iteration

## CP7 — Independent checkpoint

Roadmap считается пройденным, когда менти может самостоятельно:

- определить требования;
- выбрать архитектуру;
- выбрать технологический стек;
- отказаться от ненужных компонентов;
- объяснить trade-offs;
- управлять AI-agents;
- проверить результат;
- довести сервис до production;
- диагностировать и исправлять реальные проблемы.

---

# Project progression

```text
Project 0 — Axyro
Diagnosis + foundations + architecture understanding
           │
           ▼
Project 1 — Structured Backend
API + Postgres + domain rules + tests
           │
           ▼
Project 2 — Async / Integration System
Queues + workers + failures + external APIs
           │
           ▼
Productionization
CI/CD + security + observability + reliability
           │
           ▼
Project 3 — Production AI Service
LLM + tools + evals + production engineering
           │
           ▼
Final Project — Own Product
Independent end-to-end engineering
```

# Mentor review loop

Для каждого существенного проекта/этапа:

```text
Problem statement
      ↓
Research / theory
      ↓
Design proposal
      ↓
Mentor design review
      ↓
Implementation with AI
      ↓
Automated checks
      ↓
Technical review
      ↓
Changes / fixes
      ↓
New constraint / failure scenario
      ↓
Checkpoint
```

Ключевой вопрос на каждом этапе:

> **Почему это решение здесь нужно и что станет хуже, если его убрать?**
