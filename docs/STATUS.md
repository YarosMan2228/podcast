# STATUS.md — текущее состояние проекта

> **Снимок: 2026-09-05**, после review-батча (см. §0)
> **Backend тесты: 398 passed** на py3.12 (`.venv`); на py3.14 один известный fail в `test_health` — баг Django 5.0 + py3.14, не наш.
> **Frontend тесты: 124 passed / 124** в Vitest (8 файлов), `eslint src` — чисто.
> **End-to-end в Docker (предыдущий снимок)**: ✅ upload → SSE стрим открывается → `: connected` → ingestion → transcription → FAILED → `event: job_failed` → стрим закрывается. Vite proxy на :5173 → backend на :8000 работает.

---

## 0a. Pro-версия (2026-09-05) — четыре фичи, по коммиту на каждую

| Коммит | Фича | Что добавлено |
|---|---|---|
| `9aaa847` | **Любой язык + транскрипт SRT/VTT** | Ограничение «только английский» снято (`TRANSCRIPTION_ALLOWED_LANGUAGES` пусто = любой). Все промпты Claude получают инструкцию писать на языке подкаста (`pipeline/prompts/languages.py`). Новый артефакт `TRANSCRIPT` (миграция 0003): читаемый текст `[MM:SS] …`, файлы `transcript.srt` / `.vtt` (`pipeline/subtitles.py`, `workers/transcript_worker.py`). В ZIP: `text/transcript.txt` + `subtitles/`. API артефакта отдаёт `files: {srt, vtt}`. |
| `04e4004` | **История эпизодов + удаление** | `GET /api/jobs?limit=N`, `DELETE /api/jobs/:id` (каскад + файлы uploads/artifacts/ZIP; `services/jobs_service.py`). Страница `/history`, ссылки с лендинга и страницы job. |
| `8ab29d3` | **Брендинг + thumbnail** | `Job.podcast_name / brand_color / logo_path` (миграция 0004). Поля принимаются upload'ом (multipart) и from_url (JSON или multipart с логотипом), валидация → `400 BRANDING_INVALID`. Quote-шаблоны используют цвет и логотип (data-URI). Новый артефакт `EPISODE_THUMBNAIL` 1280×720 (`workers/thumbnail_worker.py`, шаблон `thumbnail_default.html`). Панель «Branding & clip options» на лендинге, настройки запоминаются в localStorage. |
| (этот) | **Клипы: кроп 9:16, стили субтитров, подсказки, скачивание папок** | `Job.clip_layout` (`pad` / `crop`) и `Job.caption_style` (`karaoke` / `clean` / `boxed`), миграция 0005. Кроп: `scale=…:increase,crop=1080:1920`. Karaoke-подсветка в цвете бренда. `POST /regenerate` принимает `hint` (≤300 симв.): текст → блок `<user_request>` в промпте, видео → выбор кандидата по совпадению слов с hook/reason. Regenerate quote-графики теперь даёт другую цитату и шаблон. `GET /download?part=clips|text|graphics|subtitles` собирает ZIP папки на лету (работает и до COMPLETED). |

Новые тесты: `test_transcript_feature.py`, `test_jobs_history.py`, `test_branding_thumbnail.py`, `test_clip_options.py`; frontend: `HistoryPage.test.jsx`, `BrandingSettings.test.jsx`.

Не сделано: face-tracking (кроп по центру, не по лицу), пользовательские аккаунты (брендинг per-job), публикация в соцсети по расписанию.

## 0. Review-батч (2026-09-05) — что было сломано и что починено

Полный аудит кода против `docs/SPEC.md` + `DIVISION_OF_WORK.md`. Все тесты были зелёные, но часть SPEC не была реализована, а несколько багов не покрывались тестами.

| # | Проблема | Решение |
|---|---|---|
| 1 | **`/media/` не раздавался Django.** `core/urls.py` содержал только `api/`. Все `file_url` (превью видео/PNG) и `package_url` (ZIP) отдавали 404 — на демо ни одно превью не открылось бы. | `re_path(^media/...)` → `django.views.static.serve` из `MEDIA_ROOT` (без nginx в MVP, не гейтится на DEBUG). Кнопка "Download All" во фронте теперь ходит на `/api/jobs/:id/download` (Content-Disposition + структурный 404). |
| 2 | **Двойной bump `version` при regenerate видео** (view +1, воркер +1 → v3 на диске при v2 в ответе API; transient-retry ffmpeg делал +1 ещё раз). | Воркер больше не трогает `version` — источник истины endpoint. |
| 3 | **Зависание Job на неожиданных исключениях.** Pipeline-таски ловили только типизированные ошибки + `SoftTimeLimitExceeded`; `Job.DoesNotExist`, ошибка БД, `KeyError` в данных → таск падал, Job навсегда в INGESTING/TRANSCRIBING/ANALYZING/GENERATING, UI крутится. То же в video worker → артефакт вечно PROCESSING, packaging не стартует. | `except Exception` → `_fail_job_from_unexpected` (`INGESTION_ERROR` / `TRANSCRIPTION_ERROR` / `ANALYSIS_ERROR` / `ORCHESTRATE_ERROR` + drain QUEUED артефактов); video worker → `CLIP_INTERNAL_ERROR`. |
| 4 | **Retry-политика text/quote воркеров.** Любое исключение → `self.retry()` с дефолтным countdown Celery **180 с** × 3 = ~9 минут до FAILED, включая заведомо постоянные ошибки (401 Anthropic, нет eligible цитат, битый JSON). | `is_permanent_error()` (ClaudeError non-transient, DoesNotExist, ValueError/KeyError/TypeError/AttributeError) → FAILED сразу; transient → backoff 1/2/4 с (rules/celery-tasks.md §6). |
| 5 | **Rate limit на regenerate (SPEC §6.5)** отсутствовал. | 3 запроса / артефакт / минуту → `429 REGENERATE_RATE_LIMITED` + `Retry-After`. Хранится в Django cache (Redis в проде, LocMem в тестах; `REGENERATE_LIMIT_PER_MINUTE=0` выключает). |
| 6 | **Regenerate на COMPLETED job — ZIP не пересобирался**, "Download All" отдавал старую версию. | `check_and_trigger_packaging` на COMPLETED диспатчит `package_job(repackage=True)`: пересборка ZIP без смены статуса, swap `package_path`, удаление старого архива, новый `completed` event. |
| 7 | **Фронт после regenerate на COMPLETED job не обновлялся** — SSE закрыт, polling остановлен (терминальный статус), карточка "processing" висела до F5. | `useJob`: пока на терминальном job есть QUEUED/PROCESSING артефакты — polling каждые 3 с, останавливается сам. + тесты `useJob.real.test.jsx` (real-режим хука с fake fetch/EventSource). |
| 8 | **Quote graphics: всегда 5 слотов** → при <5 eligible цитат дубликаты PNG (modulo), при 0 — пять FAILED. SPEC §7.4: "рендерим сколько есть". | Оркестратор создаёт `min(5, len(eligible))` слотов. |
| 9 | **`.claude/rules`, `.claude/agents`, `.claude/skills` были удалены** в коммите `3957e62` (Day 3 Person B), потом `.claude/` попал в `.gitignore`. CLAUDE.md, README и docstrings (`celery-tasks.md §6`, `ffmpeg-usage.md §3`…) ссылались на несуществующие файлы. | Восстановлены из `3957e62^`; `.gitignore` теперь игнорирует только `.claude/settings*.json`. |
| 10 | **Upload отклонял `application/octet-stream`** (Windows-браузеры так шлют `.m4a`/`.mkv`/`.opus`). SPEC §2.5: формат по содержимому, не по заголовку. | `resolve_upload_mime()` — fallback по расширению (явная таблица + `mimetypes`). |
| 11 | **Тесты писали ZIP-ы в `media/packages/` репозитория** (180 файлов накопилось). | `tests/settings_test.py`: `MEDIA_ROOT` → temp dir. Мусор удалён. |
| 12 | **Нет `.dockerignore`** — `COPY . .` тащил в образ `.venv`, `node_modules`, `media/` и **реальный `.env`**. | Добавлен. |
| 13 | `docker compose up` требовал ручного `migrate`. | `app` command: `migrate --noinput && runserver`. |
| 14 | ESLint error в `useJob.js` (запись в ref во время рендера, `react-hooks/refs`). | `terminalRef` обновляется в `useEffect`. |
| 15 | Мелочи: `_split_tweet_at_limit` резал только один раз (хвост >270); backslash в `file_path` на Windows → битые URL; SSE не закрывал redis-клиент; `UrlInput` обещал Spotify/SoundCloud; "any length" на лендинге при лимите 180 мин. | Исправлено. |

Новые тесты: `tests/test_review_fixes.py` (25) + `frontend/src/test/useJob.real.test.jsx` (2). Изменены 2 существующих (regenerate-тесты воркера теперь эмулируют bump версии endpoint-ом).

**Не сделано (осознанно):** Day-7 пункты требуют реальных ключей/железа — прогон 3–5 реальных подкастов, load-test 3 параллельных upload, pre-cached demo job, видео-демо и pitch deck. SPEC §4.4 шаг 3 (retry "be more generous" при <5 клипов с score ≥6) не реализован — SPEC §4.5 одновременно говорит "берём top-5 даже с низким скором"; оставлен pydantic-минимум 2 кандидата.

---

## 1. Хронология коммитов

### Day 1 — Skeleton
- `dedd27e` Django/DRF skeleton + `/api/health`
- `06c66b7` модели `Job`/`Transcript`/`Analysis`/`Artifact` + миграция `0001_initial`
- `fa27e8e` rename app `models` → `jobs` (убрали self-shadowing)
- `ae92061` `POST /api/jobs/upload`
- `b2e2670` Celery `start_job` stub + `transition_job_status` + `services.events.publish`
- `b929385` Day 1 Person B (2 задачи)
- `5f9a8ad` Day 1 Person B (React + Vite + Tailwind, mocks, Dropzone, useJob mock-режим)

### Day 2 — Pipeline стадии
- `0663175` ingestion: `normalize_to_wav` + `probe_duration_sec` + `ingest_job`
- `ae70060` Whisper client + `pipeline/transcription.py` + Celery chain
- `5804f26` Claude client + `pipeline/analysis.py` + Celery chain (один структурированный JSON-вызов)
- `e6a5c2e` Day 2 Person B (JobPage с моками, ArtifactCard варианты, ToneSelector, copy-to-clipboard)

### Day 3 — Fan-out артефактов
- `7eba2d3` Day 3 Person A: video clips + orchestrator + Jobs API
  - ASS-subtitles генератор с word-level karaoke (`pipeline/ass_subtitles.py`)
  - FFmpeg pipeline для вертикальных 9:16 клипов (`pipeline/ffmpeg_clip.py`)
  - `orchestrate_artifacts` fan-out
  - `GET /api/jobs/:id` + SSE `GET /api/jobs/:id/events`
- `3957e62`, `58726c5` Day 3 Person B: text artifact workers (LinkedIn, Twitter, Show Notes, Newsletter, YouTube Description)

### Day 4 — Интеграция
- `cbdd90f` Day 4 Person B
  - Quote graphic worker + Playwright renderer + 2 шаблона (`minimal_dark`, `gradient_purple`)
  - `POST /api/artifacts/:id/regenerate` (для всех типов)
  - `orchestrate_artifacts`: + 5 text artifacts + 5 quote graphics
  - Frontend: `USE_REAL_API=true`, EventSource на `/api/jobs/:id/events`, кнопки regenerate

### Day 5 — URL ingestion + Packaging
- `b2e5373` Day 5 Person A
  - yt-dlp URL ingestion (`pipeline/url_ingestion.py`) + расширение `ingest_job` для `source_type=URL`
  - `POST /api/jobs/from_url` (whitelist YouTube; Spotify/SoundCloud → `URL_UNSUPPORTED_HOST`)
  - `workers/packager.py` — ZIP с папками `clips/`/`text/`/`graphics/` + `index.txt`; partial packaging при failed артефактах
  - `check_and_trigger_packaging` хук в каждом воркере
  - Рефакторинг `orchestrate_artifacts` в две фазы (create-all-then-dispatch) — фикс race под eager Celery
  - `GET /api/jobs/:id/download` ZIP streaming (404 `PACKAGE_NOT_READY`)
  - `Job.package_path` + миграция `0002_job_package_path`
  - `package_url` в `_serialize_job` теперь реальный URL под `MEDIA_URL`
- `0010bb5` Day 5 Person B
  - `frontend/src/api/client.js` — обёртка `uploadFile`/`submitUrl`/`regenerateArtifact` с парсингом `{error: {code,message,field}}`
  - `LandingPage.jsx` — реальные POST'ы с `useNavigate` на `/jobs/:id`
  - `JobPage.handleRegenerate` через `regenerateArtifact` из client
- `981f1e0` docs: первый STATUS.md (Person A, после Day 5)

### Day 6 — Hardening + Polish
- `138ba16` Day 6 Person B
  - `Toaster.jsx` (pub/sub через `window.dispatchEvent('app:toast')`) + `api/toast.js`
  - Accessibility-проходка: `aria-label`/`role`/`aria-busy` + семантические `<main>`/`<header>`/`<section>`/`<article>`/`<ol>`/`<ul>`
  - Skeleton loaders в `ArtifactCard` (QUEUED/PROCESSING)
  - Refactor `useJob` на `useReducer` (поведение не изменилось)
  - Mobile-responsive: `sm:` брейкпоинты в Hero/Header
- `cc9512d` Post-Day-6 frontend fixup (Person A → Person B territory)
  - `useJob.USE_REAL_API` теперь `import.meta.env.MODE !== 'test'` — Vitest снова видит mock-режим
  - `LandingPage.handleUrl` обёрнут в try/catch → `setUploadError` (раньше URL-ошибки летели как unhandled rejection)
  - `LandingPage.test.jsx` — assertions под новые "Upload your episode"/"AI extracts the gold"/"Download your content pack"
  - `useJob.test.jsx` — удалён тест на removed-в-Day-4 merge-логику + неиспользуемый импорт
- `dd3bf0d` Day 6 Person A: hardening
  - `SoftTimeLimitExceeded` handlers во всех 4 artifact workers + 4 pipeline tasks + packager → терминальное состояние вместо зависания в PROCESSING/GENERATING
  - `orchestrate_artifacts`: при таймауте сливает stale `QUEUED` артефакты в `FAILED` (иначе packaging висит навсегда)
  - `FFmpegClipError.transient` flag + детектор по stderr ("Invalid data", "Connection reset", "Server returned 5xx", "could not seek", "Resource temporarily unavailable")
  - Video clip retry 1× через `self.retry(countdown=2)` на transient ffmpeg failure
  - `OSError(ENOSPC)` в yt-dlp → `STORAGE_FULL` (отдельно от `URL_YTDLP_FAILED`)
  - `job_id` прокинут в логи `normalize_to_wav` / `probe_duration_sec` / `download_from_url` / `build_vertical_clip` (опц. keyword-only)
  - +20 тестов в `tests/test_hardening_day6.py`
- `0343d7a` post-Day-6 cleanup
  - `docker-compose.yml` worker: `-Q default,video,text_artifacts,graphics` (без `-Q` задачи на video/text/graphics очередях висели на брокере — в eager-тестах не воспроизводилось)
  - `.gitignore`: добавлены `.claude/`, `.coverage`, `htmlcov/`
  - `video_clip_worker._render_clip`: best-effort delete предыдущего `clip_<idx>_v<N>.mp4` после успешной записи `_v<N+1>.mp4` (regenerate cleanup, тех долг)
  - `_fail_job` + 4 FAILED-ветки `package_job`: стампят `completed_at` (раньше оставалось `null` на FAILED)
  - +4 теста, итого 24 в hardening-сюите

### Docker smoke (post-Day-6 batch 2) — 5 реальных багов нашлось при первом запуске
- `Dockerfile`: `python:3.12-slim` сейчас указывает на Debian trixie, где Playwright `--with-deps chromium` падает (`ttf-ubuntu-font-family`/`ttf-unifont` removed) → pin'нул `python:3.12-slim-bookworm`
- `docker-compose.yml` worker `command: celery -A core ...` падал с `ModuleNotFoundError: core` — Celery boots без `manage.py`, который добавляет `src/` в `sys.path` → добавил `PYTHONPATH=/app/src` в env worker
- `core/celery.py` `autodiscover_tasks(["workers"])` находил только `workers/tasks.py` → артефактные воркеры (video/text/quote/packager) не были зарегистрированы в worker-процессе (работало только если orchestrate запустился раньше регенерата). Добавил 4 явных `autodiscover_tasks(..., related_name="<module>")` (lazy, не требует django.setup на module-load)
- `requirements.txt` `openai==1.30.1` крэшил при init с `httpx>=0.28` (`Client.__init__() got an unexpected keyword argument 'proxies'`) → bumped до `openai==1.55.3`
- `services/whisper_client.py` `_permanent_exceptions` содержал только `BadRequestError` → 401 `AuthenticationError` (subclass `APIError`) попадал в `_retryable_exceptions`, ретраился 4 раза × $$ на каждом неверном ключе. Добавил `AuthenticationError`, `PermissionDeniedError`, `NotFoundError` в permanent
- `api/views/jobs.py:job_events` ставил `Connection: keep-alive` — это hop-by-hop header, WSGI (PEP 3333) запрещает приложению его эмитить, Django runserver assert'ит и SSE возвращал 500. Удалил (keep-alive — default для streaming responses)
- `docker-compose.yml`: app и worker имели отдельные `build:` → пересборка одного оставляла другой на старом image (потерял полчаса на этом — apgr openai в app, worker остался с 1.30.1). Объединил в общий `image: podcast-pack:latest`, worker зависит от app
- (косметика) `docker-compose.yml`: убран obsolete `version: "3.9"` (warning на каждой команде)

### Demo-fix batch — preflight + терминальный SSE + frontend FAILED card
- `f9e4804` **preflight**: gate uploads на placeholder/пустые API ключи
  - `services/preflight.py` — `check_api_keys(probe_network=False)` структурный (regex против place/your-key/xxx/<>/replace/todo/change-me/sk-...) + опциональный `probe_network=True` (OpenAI `models.list` + Anthropic 1-token messages, кэш 60s)
  - Новый `ServiceNotConfigured` (503 `SERVICE_NOT_CONFIGURED`) + gate в `upload` и `from_url` views ДО создания Job
  - Management command `python manage.py preflight [--probe]`
  - `JobsConfig.ready()` логирует preflight на startup (skip для migrate/test/etc)
  - +25 тестов (`test_preflight.py` + `test_upload_preflight_gating.py`)
  - `tests/conftest.py` — autouse fixture с fake-real ключами для всей сюиты
- `5e64f59` **SSE cleanup**: dedicated `job_failed` event при FAILED
  - `_fail_job` теперь публикует `status_changed` (как раньше) + новый `job_failed{status, code, error}`
  - `_sse_stream` уже закрывался на `job_failed` — теперь это реально срабатывает
  - Убрана leak-ситуация: SSE стрим не закрывался на FAILED, держал Redis pub/sub forever
  - +4 теста (TestFailJobEmitsTerminalEvents + TestSSEStreamClosesOnTerminalEvents)
- `5d96cf1` **frontend FAILED handling**: stop SSE+polling, proper error card
  - `useJob`: `TERMINAL_STATUSES = {COMPLETED, FAILED}`; `useEffect(job?.status)` закрывает EventSource + cancel polling; polling fallback тоже останавливается на терминальном статусе; `es.onerror` смотрит на `terminalRef` чтобы не показывать "Connection lost" при ожидаемом закрытии после FAILED; новый event `job_failed` → fetchJob() для подтягивания `Job.error`
  - `JobPage` FAILED branch: красный ✕ icon, semantic `<h1 role="alert">`, `<p>` с `whitespace-pre-wrap break-words` для длинных error strings, "Try again" → `useNavigate('/')` (SPA, без full reload)
  - +5 тестов (TestFailedBranch в `JobPage.test.jsx`): красный headline + persisted error, fallback на null, SPA navigation, нет "Connection lost", нет progressbar

---

## 2. Архитектура: 5 слоёв pipeline

```
upload/from_url   →   start_job        →   transcribe_job_task   →   analyze_job_task
[POST /api/jobs/]    [INGESTING]            [TRANSCRIBING]            [ANALYZING]
                     ffmpeg normalize       Whisper API               Claude API (один JSON)
                     ffprobe duration       chunking + stitching      themes/clips/quotes/chapters

         ↓
orchestrate_artifacts          fan-out (3 очереди)
[GENERATING]               ┌──────────────┬──────────────┬─────────────────┐
                           ↓              ↓              ↓                 ↓
                     video_clip_worker  text_artifact_worker (×5)   quote_graphic_worker
                     queue=video        queue=text_artifacts        queue=graphics
                     ffmpeg + ASS subs  Claude per type             Playwright HTML→PNG

                     каждый воркер на терминальном состоянии → check_and_trigger_packaging

                                            ↓
                                    package_job
                                    [PACKAGING → COMPLETED]
                                    ZIP в media/packages/
                                    SSE event "completed" → package_url
```

---

## 3. Тесты — текущее покрытие

### Backend (pytest, `tests/`)

| Модуль | Файл | Тестов |
|---|---|---:|
| Errors envelope | `test_errors.py` | 8 |
| Health | `test_health.py` | 3 (1 fails — py3.14, см. вверху) |
| Settings boot | `test_settings_boot.py` | 4 |
| Models + transitions | `test_models.py` | 16 |
| Upload view | `test_upload.py` | 14 |
| URL ingestion | `test_url_ingestion.py` | 18 |
| Ingestion (ffmpeg/ffprobe) | `test_ingestion.py` | 16 |
| Whisper client | `test_whisper_client.py` | 6 |
| Transcription pipeline | `test_transcription.py` | 16 |
| Claude client | `test_claude_client.py` | 6 |
| Analysis pipeline | `test_analysis.py` | 20 |
| ASS subtitles | `test_ass_subtitles.py` | 17 |
| FFmpeg clip builder | `test_ffmpeg_clip.py` | 10 |
| Video clip worker | `test_video_clip_worker.py` | 7 |
| Text artifact workers | `test_text_artifacts.py` | 39 |
| Quote graphic worker | `test_quote_graphic_worker.py` | 17 |
| Workers/orchestrator | `test_workers.py` | 25 |
| Jobs API + SSE | `test_jobs_api.py` | 14 |
| Regenerate endpoint | `test_regenerate.py` | 17 |
| Packager | `test_packager.py` | 13 |
| Download endpoint | `test_download.py` | 11 |
| Events publisher | `test_events.py` | 4 |
| Pipeline integration | `test_pipeline_integration.py` | 2 |
| `run_pipeline` mgmt cmd | `test_run_pipeline_command.py` | 8 |
| Day-6 hardening | `test_hardening_day6.py` | **28** |
| Preflight (structural + probe) | `test_preflight.py` | **17** |
| Upload preflight gating | `test_upload_preflight_gating.py` | **8** |
| **Итого** | | **361** |

Прогон: `python -m pytest tests/` или (исключая известный py3.14 fail) `python -m pytest tests/ --ignore=tests/test_health.py`.

### Frontend (Vitest, `frontend/src/test/`)

**122 passed / 122** в ~4s. 7 файлов: `App.test.jsx`, `Dropzone.test.jsx`, `JobPage.test.jsx` (+5 новых для FAILED branch), `LandingPage.test.jsx`, `mocks.test.js`, `UrlInput.test.jsx`, `useJob.test.jsx`. Прогон: `cd frontend && npm test`.

---

## 4. API endpoints

| Метод + путь | Назначение | View |
|---|---|---|
| `GET /api/health` | Healthcheck | `api/views/health.py` |
| `POST /api/jobs/upload` | Multipart файл → `{job_id, status: PENDING}` (201) | `api/views/upload.py:upload` |
| `POST /api/jobs/from_url` | `{url}` → `{job_id, status}` (201). Whitelist: только YouTube (SPEC §2.4) | `api/views/upload.py:from_url` |
| `GET /api/jobs/:id` | Полное состояние Job (SPEC §9.3): status, progress counters, analysis, artifacts[], package_url, error | `api/views/jobs.py:get_job` |
| `GET /api/jobs/:id/events` | SSE stream Redis pub/sub `job:<uuid>` | `api/views/jobs.py:job_events` |
| `GET /api/jobs/:id/download` | ZIP stream `Content-Disposition: attachment` (404 `PACKAGE_NOT_READY`) | `api/views/jobs.py:download_package` |
| `POST /api/artifacts/:id/regenerate` | `{tone?}` → `{artifact_id, status: QUEUED, version}` (202) | `api/views/jobs.py:regenerate_artifact` |

### SSE events
- `status_changed` — `{status}` (любая стадия pipeline)
- `artifact_ready` — `{artifact_id, type, index}`
- `artifact_failed` — `{artifact_id, error}`
- `completed` — `{package_url}` (терминальный — стрим закрывается)
- `job_failed` — `{status, code, error}` (терминальный — стрим закрывается)

### Error codes (для frontend branching)
- `SERVICE_NOT_CONFIGURED` (503) — preflight не пропустил, ключи placeholder/пусты. Frontend должен показать "Server is not configured" + дать кнопку retry без перезагрузки страницы.
- `UPLOAD_*` (400/413) — клиентские ошибки upload (no_file, too_large, invalid_format, empty)
- `URL_INVALID` / `URL_UNSUPPORTED_HOST` (400) — URL-валидация
- `URL_YTDLP_FAILED` (422) — yt-dlp content error (live stream, geo-block, deleted)
- `JOB_NOT_FOUND` / `ARTIFACT_NOT_FOUND` (404)
- `PACKAGE_NOT_READY` (404) — download когда status != COMPLETED
- `INVALID_TONE` (400) — regenerate с неподдерживаемым tone
- `STORAGE_ERROR` / `STORAGE_FULL` (500) — сервер сломан, не клиент

---

## 5. Migrations

- `0001_initial` — `Job` / `Transcript` / `Analysis` / `Artifact` + enums
- `0002_job_package_path` — `Job.package_path` (Day 5)

Применить: `docker compose run --rm app python manage.py migrate`.

---

## 6. Что закрыто в post-Day-6 cleanup

### `0343d7a`
| Тех долг | Решение |
|---|---|
| `docker-compose.yml` worker без `-Q` — задачи на video/text/graphics очередях висят в проде | `-Q default,video,text_artifacts,graphics` |
| `.claude/` и `.coverage` всегда untracked | в `.gitignore` |
| Regenerate video clip накапливает старые mp4 на диске | `_render_clip` после DB swap best-effort удаляет предыдущую версию |
| `completed_at = null` на FAILED jobs | `_fail_job` + 4 FAILED-ветки `package_job` стампят `completed_at = djtz.now()` |

### `f9e4804` / `5e64f59` / `5d96cf1` — Demo-fix batch
| Симптом на демо | Решение |
|---|---|
| Файл загружался успешно, через 30s показывал TRANSCRIPTION_INVALID_INPUT (placeholder OPENAI_API_KEY) | preflight в upload views возвращает 503 `SERVICE_NOT_CONFIGURED` ДО создания Job |
| После FAILED появлялся "Connection lost — polling for updates" + сёрвер держал Redis pubsub forever | `_fail_job` шлёт `job_failed` event; SSE `_sse_stream` закрывается на нём; frontend `useJob` смотрит `terminalRef` в `es.onerror` чтобы не пугать пользователя |
| Polling крутился вечно после FAILED ("0/0 artifacts ready") | `useJob` `useEffect(job?.status)` закрывает EventSource + cancel polling на `COMPLETED`/`FAILED` |
| "Try again with a different file" — `<a href>` с full reload | `<button onClick={() => navigate('/')}>` — SPA, без reload |

### Docker smoke (`d21a0a5`) — 7 багов первого запуска
| Баг | Решение |
|---|---|
| Build падал — Playwright `--with-deps` не находит `ttf-ubuntu-font-family`/`ttf-unifont` (Debian trixie removed) | Pin `python:3.12-slim-bookworm` |
| Worker крэшил с `ModuleNotFoundError: core` | `PYTHONPATH=/app/src` в worker env |
| Из 12 Celery tasks регистрировалось только 4 — `autodiscover_tasks` ищет `tasks.py`, артефактные воркеры в отдельных модулях | 4 явных `autodiscover_tasks(..., related_name="<module>")` (lazy, ждёт django.setup) |
| `openai==1.30.1` × `httpx>=0.28` → `TypeError: 'proxies'` при init OpenAI клиента | Bumped до `openai==1.55.3` |
| 401 `AuthenticationError` ретраился 4 раза × $$ (попадал в `_retryable_exceptions` через parent `APIError`) | Добавлены `AuthenticationError`/`PermissionDeniedError`/`NotFoundError` в `_permanent_exceptions` |
| SSE `/api/jobs/:id/events` возвращал 500 — `Connection: keep-alive` это hop-by-hop, WSGI запрещает | Header убран (keep-alive это default для streaming responses) |
| App+worker раздельные `build:` → апгрейд деп в одном оставлял другой на старом image | Общий `image: podcast-pack:latest`, worker `depends_on: [..., app]` |
| Obsolete `version: "3.9"` в compose | Удалён |

---

## 7. Что осталось (Day 7 — Demo prep)

### Person A
- [ ] Прогон 3–5 реальных подкастов через full pipeline, cherry-pick лучший для демо
- [ ] Load test: 3 параллельных upload в одном demo окне (проверить что worker concurrency=4 справляется)
- [ ] Pre-cached demo job на случай wifi-сбоя (заранее загнанный пакет, прямой URL для показа)

### Person B
- [ ] 90-сек видео-демо для pitch-деки (на случай wifi-сбоя)
- [ ] Pitch deck — 5 слайдов: Problem / Solution / Live Demo / Architecture / Ask

### Совместное
- [ ] Финальный rehearsal демо-сценария (дважды подряд)

---

## 8. Что нужно от пользователя (внешнее, не делается из кода)

| Что | Зачем | Куда |
|---|---|---|
| `OPENAI_API_KEY` | Whisper транскрипция (без него preflight даёт 503 на upload) | `.env` файл в корне |
| `ANTHROPIC_API_KEY` | Claude analysis + все text artifacts (без него preflight даёт 503) | `.env` файл в корне |
| `docker compose up -d db redis app worker` | Полный стек | один раз |
| `docker compose run --rm app python manage.py migrate` | Применить миграции | при первом запуске |
| `docker compose run --rm app python manage.py preflight --probe` | Проверить что ключи приняты вендорами | после правки `.env` |
| Один реальный подкаст (mp3/mp4, 5–10 мин) | Интеграционный прогон upload → ZIP вручную | drag-and-drop на `localhost:5173` |

---

## 9. Smoke-сценарии (руками после demo-fix)

Поднять стек: `docker compose up -d` (если не запущен), Vite: `cd frontend && npm run dev`. Открыть `http://localhost:5173`.

### Сценарий 1 — preflight ловит placeholder ключ (быстро, без файла)
1. В `.env` оставь `OPENAI_API_KEY=sk-placeholder-replace-me`
2. `docker compose restart app`
3. `curl -X POST -F "file=@any.wav;type=audio/wav" http://localhost:8000/api/jobs/upload`
**Ожидание:** HTTP 503, body `{"error": {"code": "SERVICE_NOT_CONFIGURED", "message": "OPENAI_API_KEY: ... appears to be a placeholder ..."}}`. Job в БД НЕ создан.

### Сценарий 2 — преflight через CLI
- `docker compose run --rm app python manage.py preflight`
**Ожидание:** exit 0 если ключи валидны, exit 1 со списком если placeholder/пустые.
- `docker compose run --rm app python manage.py preflight --probe`
**Ожидание:** дополнительно ходит в OpenAI/Anthropic; 401 → exit 1.

### Сценарий 3 — happy path с реальными ключами
1. В `.env` поставь живые `OPENAI_API_KEY` + `ANTHROPIC_API_KEY`
2. `docker compose restart app worker`
3. На `http://localhost:5173` drop файл (5-10 мин mp3/mp4)
**Ожидание:** редирект на `/jobs/<uuid>` → SSE подключился (NetworkTab → status 200, Type=eventsource) → status_changed: INGESTING / TRANSCRIBING / ANALYZING / GENERATING → artifact_ready × N → completed → "Download All (ZIP)" доступен.

### Сценарий 4 — kill worker mid-processing
1. Запусти upload (большой файл, чтобы было время)
2. Когда status = ANALYZING или GENERATING, в другом терминале: `docker compose stop worker`
3. Через 5-10s в UI должно появиться "Connection lost — polling for updates…"
4. `docker compose start worker`
**Ожидание:** SSE переподключится (банер исчезнет), artifact_ready начнут идти.

### Сценарий 5 — middle-of-pipeline failure
1. Поставь живой `OPENAI_API_KEY`, **placeholder** `ANTHROPIC_API_KEY`. Restart app+worker.
2. Drop файл на `:5173`
**Ожидание:** PENDING → INGESTING → TRANSCRIBING (Whisper отработает) → ANALYZING → FAILED. UI показывает красную карточку "Processing failed" с текстом ошибки Anthropic 401, кнопка "Try again". SSE стрим закрылся (NetworkTab → eventsource завершён). Polling **НЕ** запустился (нет повторных GET /api/jobs/<id> в Network tab).

### Логи для диагностики
```bash
# Все ошибки с воркера и app:
docker compose logs -f worker app | findstr /i "error fail traceback exception"
# Только воркер по конкретному job_id:
docker compose logs worker | findstr <uuid>
# SSE-сообщения по нашим event-name:
docker compose logs worker | findstr "status_changed job_failed artifact_ready completed"
```

---

## 10. Известные ограничения / технический долг (остаётся)

- **yt-dlp**: только YouTube (SPEC §2.4). Spotify/SoundCloud → `URL_UNSUPPORTED_HOST` намеренно.
- **Packaging idempotency**: повторный `package_job` на COMPLETED — no-op (правильно). Если регенерируешь артефакт после COMPLETED — ZIP **не** пересобирается. По SPEC так задумано (regenerate отдаёт превью). Если нужно пересборку — добавить `repackage=true` параметр; пока не нужно.
- **Двойной bump `version`** при regenerate: view (`api/views/jobs.py:regenerate_artifact`) делает `version+=1`, потом video worker с `regenerate=True` делает ещё `+1`. Реальная версия в DB после одного regenerate — N+2. v(N+1) "теряется" — не критично (UI всё равно показывает свежее через poll/SSE), но это API-контракт изменение, отложено до post-MVP.
- **Только английский язык в Whisper** (`pipeline/transcription.py:SUPPORTED_LANGUAGE`). Job с русским подкастом → `TRANSCRIPTION_UNSUPPORTED_LANGUAGE`.
- **`.env`** не в репо (правильно). Локально создать вручную из `.env.example`.
- **ffmpeg/ffprobe** не установлены на Windows-хосте → локальный non-docker запуск не сработает. Docker image содержит.
- **Python 3.14 + Django 5.0** — `test_health::test_unknown_endpoint_returns_404` падает в `Context.__copy__`. Это django bug, не наш код. В prod-образе python 3.12 — проходит.

---

## 11. Точки входа в код / документация

- **`docs/PROJECT_IDEA.md`** — что строим и зачем
- **`docs/SPEC.md`** — ground truth по контрактам (читать перед изменениями!)
- **`docs/DIVISION_OF_WORK.md`** — кто что делает, матрица зависимостей
- **`CLAUDE.md`** + `.claude/rules/` — правила Claude Code для этого проекта
- **`tests/conftest.py`** + `tests/settings_test.py` — pytest bootstrap (SQLite in-memory, eager Celery, `EVENTS_ENABLED=False`)

### Ключевые модули

| Модуль | Что внутри |
|---|---|
| `src/api/views/upload.py` | POST `/jobs/upload`, `/jobs/from_url` (валидация + dispatch) |
| `src/api/views/jobs.py` | GET state, SSE, download, POST regenerate |
| `src/api/errors.py` | `ApiError` базовый + 12 структурированных кодов (`UPLOAD_*`, `URL_*`, `JOB_NOT_FOUND`, ...) |
| `src/pipeline/ingestion.py` | `save_upload`, `normalize_to_wav`, `probe_duration_sec`, `ingest_job` |
| `src/pipeline/url_ingestion.py` | `validate_url` (whitelist), `download_from_url` (yt-dlp Python API) |
| `src/pipeline/transcription.py` | Whisper orchestration: chunking, stitching, edge-case gates (empty/lang/noise) |
| `src/pipeline/analysis.py` | Один Claude вызов + dedupe overlapping clips + 3-step retry на schema |
| `src/pipeline/ffmpeg_clip.py` | `build_vertical_clip` (видео + audio-only waveform), `_is_transient_ffmpeg_failure` |
| `src/pipeline/ass_subtitles.py` | ASS subs с word-level karaoke highlighting |
| `src/services/whisper_client.py` | OpenAI обёртка с 4-retry exponential backoff |
| `src/services/claude_client.py` | Anthropic обёртка + prompt caching |
| `src/services/events.py` | Redis pub/sub `publish(job_id, event, data)` |
| `src/services/graphic_renderer.py` | Playwright HTML→PNG для quote graphics |
| `src/workers/tasks.py` | 4 pipeline tasks + `transition_job_status` + `_fail_job` + `check_and_trigger_packaging` |
| `src/workers/video_clip_worker.py` | `_render_clip` + transient retry + cleanup старых версий |
| `src/workers/text_artifact_worker.py` | 5 task'ов (LinkedIn/Twitter/ShowNotes/Newsletter/YouTube) через общий scaffold `_run_artifact_task` |
| `src/workers/quote_graphic_worker.py` | Playwright рендер + select_eligible_quotes (20–180 chars) |
| `src/workers/packager.py` | ZIP с `clips/`/`text/`/`graphics/` + `index.txt` + partial packaging |
| `frontend/src/api/client.js` | `uploadFile`/`submitUrl`/`regenerateArtifact` с парсингом структурированных ошибок |
| `frontend/src/api/toast.js` + `components/Toaster.jsx` | Pub/sub через `window` event, без context |
| `frontend/src/hooks/useJob.js` | `useMockJob` + `useRealJob` (SSE + polling fallback), переключение по `import.meta.env.MODE` |
| `frontend/src/pages/JobPage.jsx` | Progress + results, regenerate с toast feedback |
| `frontend/src/pages/LandingPage.jsx` | Hero + Dropzone + UrlInput + "How it works" |
