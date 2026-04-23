# DIVISION_OF_WORK.md — Разделение работы между двумя разработчиками

> Документ для координации двух людей на 7-дневном хакатоне. Цель: минимизировать блокировки, максимизировать параллельность, зафиксировать контракты между зонами ответственности.

---

## Принципы разделения

1. **Критерий разделения — зона ответственности в рантайме, не технология.** Оба пишут Python, оба могут читать фронтенд. Делимся по: "что ломается, когда не работает".
2. **Интерфейсный контракт фиксируется в Day 1.** Это REST API + SSE events из `SPEC.md §9.3`. После Day 1 изменения контракта — только с обязательным mini-sync между двумя людьми.
3. **Каждый может работать в полной изоляции в Day 1–3** благодаря моковым данным.
4. **Интеграция — Day 4.** Выделенный день на стыковку, никаких новых фич.
5. **Общая территория — CLAUDE.md, модели Django, миграции БД.** Редактируем по очереди, коммитим с осмысленными сообщениями, делаем rebase перед push'ом.

---

## Person A — Backend & Media Pipeline

### Зона ответственности

- Всё, что работает на сервере в обработке **медиа и LLM**
- Конвертация, транскрипция, анализ, генерация видео-клипов
- Celery инфраструктура и оркестрация
- Интеграция с OpenAI Whisper и Anthropic Claude APIs

### Модули из SPEC.md

- §2 Ingestion
- §3 Transcription
- §4 Analysis
- §5 Video Clips
- §9 Job Orchestration (серверная часть: REST API + SSE endpoint + Celery оркестратор)

### Файлы, которые Person A создаёт

```
src/
├── api/
│   └── views/
│       ├── upload.py          # POST /api/jobs/upload, /api/jobs/from_url
│       └── jobs.py            # GET /api/jobs/:id, SSE endpoint, download
├── pipeline/
│   ├── ingestion.py
│   ├── transcription.py
│   ├── analysis.py
│   ├── ffmpeg_clip.py         # сборка ffmpeg команд для клипов
│   ├── ass_subtitles.py       # генерация .ass файлов с karaoke-highlight
│   └── prompts/
│       └── analysis.py        # промпт для семантического анализа
├── services/
│   ├── whisper_client.py
│   └── claude_client.py       # ЕДИНЫЙ клиент Claude для обоих людей
├── workers/
│   ├── __init__.py
│   ├── tasks.py               # Celery tasks orchestrator (chain: transcribe → analyze → fan-out)
│   └── video_clip_worker.py
├── jobs/                       # Django app с моделями
│   ├── apps.py                 # JobsConfig
│   ├── enums.py                # JobStatus, ArtifactType, ArtifactStatus, SourceType + транзишны
│   ├── job.py                  # Job model
│   ├── transcript.py
│   ├── analysis.py
│   ├── artifact.py             # общая таблица, shared с Person B
│   ├── managers.py
│   ├── models.py               # Django entry point: re-export всех моделей и enum-ов
│   └── migrations/
└── core/
    ├── settings.py
    ├── celery.py
    └── urls.py
```

### Порядок задач Person A по дням

**Day 1**
- Django + DRF skeleton, docker-compose up → 200 на `/api/health`
- Модели `Job`, `Transcript`, `Analysis`, `Artifact` + первая миграция (согласовать с Person B)
- `POST /api/jobs/upload` принимает файл, сохраняет в media, возвращает job_id, статус `PENDING`
- Заглушка Celery task `start_job(job_id)`, которая просто ставит статус `INGESTING` и спит 3 сек

**Day 2**
- Ingestion: ffmpeg нормализация, ffprobe duration
- Whisper integration, chunking длинных файлов, ретраи
- Celery chain ingestion → transcription работает end-to-end на файле 60 сек
- Анализ: `prompts/analysis.py` с промптом, Claude вызов с structured output, сохранение в `Analysis`
- Тест промпта на 3 разных 10-минутных отрывках реальных подкастов, итерация

**Day 3**
- Video clip worker: ffmpeg pipeline, ASS subtitle generator с word-highlight
- Один рабочий клип end-to-end: upload → готовый mp4 9:16 с субтитрами
- Оркестратор fan-out: `orchestrate_artifacts(job_id)` создаёт 5 VIDEO_CLIP артефактов, каждый в своём таске

**Day 4 — Integration Day (совместно с Person B)**
- SSE endpoint `GET /api/jobs/:id/events` с Redis pub/sub
- `GET /api/jobs/:id` возвращает полное состояние
- Воркеры публикуют события в Redis при обновлении артефактов
- Person B переключает свой фронт с mock API на реальный

**Day 5**
- URL ingestion через yt-dlp (только YouTube для MVP)
- Regenerate video clip: `POST /api/artifacts/:id/regenerate`
- Загрузка пакета: `GET /api/jobs/:id/download` (zip)
- Packaging task (совместная ответственность, Person A реализует, Person B проверяет)

**Day 6 — Hardening**
- Обработка всех ошибок из таблиц "Крайние случаи" в SPEC
- Retry логика: transcription, ffmpeg, claude
- Celery `soft_time_limit` на все таски, чтобы зависшие не блокировали очередь
- Логирование: каждый API call — в лог с job_id для trace

**Day 7 — Demo prep**
- Прогон 3–5 реальных подкастов, cherry-pick лучшего для demo
- Load test: 3 параллельных upload в одном demo окне
- Pre-cached demo job на случай, если wifi упадёт

---

## Person B — Frontend & Text Artifacts

### Зона ответственности

- Всё, что связано с генерацией **текстовых и графических артефактов** (не видео)
- Весь frontend (React)
- UX прогресс-индикации и превью

### Модули из SPEC.md

- §6 Text Artifacts (LinkedIn, Twitter, Show Notes, Newsletter, YouTube Description)
- §7 Quote Graphics
- §8 Packaging
- §10 Frontend

### Файлы, которые Person B создаёт

```
src/
├── workers/
│   ├── text_artifact_worker.py
│   ├── quote_graphic_worker.py
│   └── packager.py
├── pipeline/
│   └── prompts/
│       ├── linkedin.py
│       ├── twitter.py
│       ├── shownotes.py
│       ├── newsletter.py
│       └── youtube_description.py
└── services/
    └── graphic_renderer.py     # Playwright wrapper

frontend/
├── package.json
├── vite.config.js
├── tailwind.config.js
├── index.html
└── src/
    ├── main.jsx
    ├── App.jsx
    ├── hooks/
    │   └── useJob.js
    ├── pages/
    │   ├── LandingPage.jsx
    │   └── JobPage.jsx         # и Progress и Results (разные ветки рендера)
    ├── components/
    │   ├── Dropzone.jsx
    │   ├── UrlInput.jsx
    │   ├── JobProgressBar.jsx
    │   ├── ArtifactCard.jsx
    │   ├── VideoArtifact.jsx
    │   ├── TextArtifact.jsx
    │   ├── GraphicArtifact.jsx
    │   └── ToneSelector.jsx
    ├── api/
    │   ├── client.js            # fetch wrapper
    │   └── mocks.js             # фикстуры для Day 1–3
    └── quote_templates/
        ├── minimal_dark.html
        └── minimal_light.html
```

### Порядок задач Person B по дням

**Day 1**
- React + Vite + Tailwind скелет
- Роутер: `/` (landing), `/jobs/:id` (progress/results)
- `Dropzone` компонент — визуально работает, пока не шлёт запросы
- `mocks.js` с фикстурами: mock `job_state_processing`, `job_state_completed`, мок артефактов всех типов
- `useJob(jobId)` хук — пока работает только на моках (SSE через setTimeout)

**Day 2**
- `JobPage` с двумя ветками (progress / results)
- Все `ArtifactCard` варианты с mock данными выглядят финально (видео с sample.mp4, тексты из mocks.js, PNG-заглушки)
- `ToneSelector`, `copy-to-clipboard` + toast
- Запуск через `npm run dev`, демо UX полностью на моках — можно показать дизайнеру / жюри без бэка

**Day 3**
- Text artifact workers (все 5 типов): LinkedIn, Twitter, ShowNotes, Newsletter, YouTube Description
- Промпты в `src/pipeline/prompts/` + итерация на 3 реальных транскриптах
- Каждый воркер — отдельная Celery task, очередь `text_artifacts`
- Валидация ответов Claude (длины, формат JSON для Twitter)

**Day 4 — Integration Day (совместно с Person A)**
- Подключение frontend к реальному API (убираем mocks)
- SSE consumption в `useJob` — переключение с фейкового таймера на реальный EventSource
- Quote graphic worker: Playwright renderer + 2 HTML шаблона
- Регенерация text artifacts: UI + API вызов

**Day 5**
- Packaging: ZIP генерация со структурой из SPEC §8.2
- `GET /api/jobs/:id/download` streaming (Person A даёт endpoint, Person B пишет логику packaging task)
- Regenerate для всех типов артефактов с tone-селектором
- Landing page: финальный hero, gif-демо, "how it works"

**Day 6 — Polish**
- Все edge cases из SPEC §10.6 (reconnect SSE, failed artifacts, retry)
- Mobile-responsive (минимум не ломается на iPhone, не обязательно pretty)
- Accessibility: `aria-label` на кнопках, `role` на статус-индикаторах
- Visual polish: анимации переходов, skeleton loaders, toast notifications

**Day 7 — Demo prep**
- Записать 90-секундное видео-демо для pitch-деки на случай wifi-проблем
- Pitch deck: 5 слайдов (Problem / Solution / Live Demo placeholder / Architecture / Ask)

---

## Shared territory — правила редактирования

Эти файлы трогают оба. Конфликты неизбежны, поэтому — правила.

### 1. `src/jobs/` (Django-модели) и миграции БД
- Менять схему — **только по согласованию в Slack/Telegram** (2-минутный ping)
- Перед миграцией: `pull → makemigrations → check что нет conflict-миграций → apply → push`
- Если Person B меняет `Artifact.metadata_json`, Person A должен быть в курсе (поля могут читаться из разных воркеров)

### 2. `src/services/claude_client.py`
- Единый клиент с retry и prompt caching
- **Владелец файла — Person A** (он первый пишет), но Person B может добавлять методы через PR
- Не дублируем anthropic-клиент в нескольких местах

### 3. `CLAUDE.md`, `.claude/agents/*`, `.claude/rules/*`
- Редактируют оба, но ключевые правила согласовать в Day 1
- Антипаттерн: один чел добавил rule "use X library", другой об этом не знает

### 4. `docker-compose.yml`, `requirements.txt`, `Dockerfile`
- Любые изменения — обязательный ping второму человеку
- После изменений `requirements.txt` — оба пересобирают контейнер

### 5. `src/core/urls.py`
- Person A добавляет урлы для своих views
- Person B добавляет урлы для своих views  
- Конфликты в merge разрешаем через import в разных местах файла (по зонам)

---

## Интерфейсный контракт между Person A и Person B

Этот контракт **замораживается в Day 1** и меняется только синхронно.

### REST endpoints (Person A реализует, Person B потребляет)

```
POST   /api/jobs/upload           — multipart file → {job_id, status}
POST   /api/jobs/from_url         — {url} → {job_id, status}
GET    /api/jobs/:id              — полное состояние job'а (см. SPEC §9.3)
GET    /api/jobs/:id/events       — SSE stream
GET    /api/jobs/:id/download     — ZIP stream
POST   /api/artifacts/:id/regenerate  — {tone?} → {artifact_id, status, version}
```

### SSE events (Person A эмитит, Person B слушает)

```
event: status_changed   data: {status}
event: artifact_ready   data: {artifact_id, type, index}
event: artifact_failed  data: {artifact_id, error}
event: completed        data: {package_url}
```

### Shared модель `Artifact` (оба читают и пишут)

См. SPEC §5.2. Поля, которые **Person A** заполняет:
- `type=VIDEO_CLIP`, `file_path`, `metadata_json` с clip-specific полями

Поля, которые **Person B** заполняет:
- `type` одного из: `LINKEDIN_POST`, `TWITTER_THREAD`, `SHOW_NOTES`, `NEWSLETTER`, `YOUTUBE_DESCRIPTION`, `QUOTE_GRAPHIC`
- `text_content` для всех text-типов, `file_path` для QUOTE_GRAPHIC, `metadata_json`

**Ничего не дублируется.** Если Person B хочет новое поле в `Artifact` — сначала обсудить с Person A (возможно, это повод для новой таблицы).

---

## Матрица "кто блокирует кого"

| Задача Person B | Требует от Person A | Fallback |
|---|---|---|
| Тестить text artifact workers | Модель `Analysis` готова с реальными данными | Использовать mock analysis JSON fixture в `tests/fixtures/` |
| Вызывать regenerate endpoint | Endpoint реализован в §9.3 | На Day 1–3 регенерация работает только в UI, реально не идёт |
| SSE consumption в `useJob` | SSE endpoint готов | `mocks.js` симулирует SSE через `setInterval` |
| Packaging включает video-файлы | Клипы реально сгенерированы | Packaging стартует всё равно, добавляет текст + графику, в `index.txt` помечает "video clips: pending" |

| Задача Person A | Требует от Person B | Fallback |
|---|---|---|
| Проверить что fan-out на text artifacts работает | Text worker реализован | Person A пишет dummy `text_artifact_worker` стаб (возвращает "Lorem ipsum"), Person B заменяет |
| Протестить SSE end-to-end | Frontend коннектится и показывает events | Curl на SSE endpoint или html-страница с `<EventSource>` в 30 строк |

---

## Checkpoints (совместные синки)

- **Day 1 evening (15 мин)**: Показать друг другу что запустилось. Проверить что модели `Artifact` одинаково поняты. Заморозить контракт API.
- **Day 3 evening (30 мин)**: Демо своих пайплайнов друг другу. Person A показывает клип, Person B — UI с моками и text worker output.
- **Day 4 all day**: Integration. Работают рядом (физически или войс-канал). Главная задача — чтобы реальный upload производил реальные артефакты в реальном UI.
- **Day 6 evening (30 мин)**: Финальный прогон всех error-случаев. Что ломается → фиксим завтра.
- **Day 7 morning**: Полный rehearsal демо-сценария. Дважды подряд.

---

## Правила коммуникации

- Мелкие вопросы — в чат (любой мессенджер), ожидаемый ответ < 30 мин
- Архитектурные вопросы — войс, не откладывать
- Каждый вечер — короткий апдейт: что сделано / что блокирует / что нужно от второго
- Если застрял > 1 часа на одной задаче — писать второму человеку, не сидеть молча
