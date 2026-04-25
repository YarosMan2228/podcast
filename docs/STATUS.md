# STATUS.md — текущее состояние проекта

> **Снимок: 2026-04-25**, после `0343d7a` (post-Day-6 cleanup)
> **Backend тесты: 334 passed / 1 failed**
> Единственный fail — `test_health.py::test_unknown_endpoint_returns_404` — Django 5.0 + Python 3.14 несовместимость в `Context.__copy__` (ошибка в Django, не в нашем коде); в Docker-образе с Python 3.12 не воспроизводится. **Игнорируем до апгрейда Django**.
>
> **Frontend тесты: 7 файлов** под Vitest (запускаются `npm test` в `frontend/`); локально не прогонял в этой сессии — нет `node_modules`. По коду должны проходить (последний раз правились в `cc9512d`).

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
| Day-6 hardening | `test_hardening_day6.py` | **24** |
| **Итого** | | **335** |

Прогон: `python -m pytest tests/` или (исключая известный py3.14 fail) `python -m pytest tests/ --ignore=tests/test_health.py`.

### Frontend (Vitest, `frontend/src/test/`)

7 файлов: `App.test.jsx`, `Dropzone.test.jsx`, `JobPage.test.jsx`, `LandingPage.test.jsx`, `mocks.test.js`, `UrlInput.test.jsx`, `useJob.test.jsx`. Прогон: `cd frontend && npm test`.

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
- `completed` — `{package_url}`

---

## 5. Migrations

- `0001_initial` — `Job` / `Transcript` / `Analysis` / `Artifact` + enums
- `0002_job_package_path` — `Job.package_path` (Day 5)

Применить: `docker compose run --rm app python manage.py migrate`.

---

## 6. Что закрыто в post-Day-6 cleanup (`0343d7a`)

| Тех долг | Решение |
|---|---|
| `docker-compose.yml` worker без `-Q` — задачи на video/text/graphics очередях висят в проде | `-Q default,video,text_artifacts,graphics` |
| `.claude/` и `.coverage` всегда untracked | в `.gitignore` |
| Regenerate video clip накапливает старые mp4 на диске | `_render_clip` после DB swap best-effort удаляет предыдущую версию |
| `completed_at = null` на FAILED jobs | `_fail_job` + 4 FAILED-ветки `package_job` стампят `completed_at = djtz.now()` |

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
| `OPENAI_API_KEY` | Whisper транскрипция | `.env` файл в корне |
| `ANTHROPIC_API_KEY` | Claude analysis + все text artifacts | `.env` файл в корне |
| `docker compose up -d db redis` | Поднять Postgres + Redis | один раз |
| `docker compose run --rm app python manage.py migrate` | Применить миграции | при первом запуске |
| Один реальный подкаст (mp3/mp4, 5–10 мин) | Интеграционный прогон upload → ZIP вручную | drag-and-drop на `localhost:5173` |

---

## 9. Известные ограничения / технический долг (остаётся)

- **yt-dlp**: только YouTube (SPEC §2.4). Spotify/SoundCloud → `URL_UNSUPPORTED_HOST` намеренно.
- **Packaging idempotency**: повторный `package_job` на COMPLETED — no-op (правильно). Если регенерируешь артефакт после COMPLETED — ZIP **не** пересобирается. По SPEC так задумано (regenerate отдаёт превью). Если нужно пересборку — добавить `repackage=true` параметр; пока не нужно.
- **Двойной bump `version`** при regenerate: view (`api/views/jobs.py:regenerate_artifact`) делает `version+=1`, потом video worker с `regenerate=True` делает ещё `+1`. Реальная версия в DB после одного regenerate — N+2. v(N+1) "теряется" — не критично (UI всё равно показывает свежее через poll/SSE), но это API-контракт изменение, отложено до post-MVP.
- **Только английский язык в Whisper** (`pipeline/transcription.py:SUPPORTED_LANGUAGE`). Job с русским подкастом → `TRANSCRIPTION_UNSUPPORTED_LANGUAGE`.
- **`.env`** не в репо (правильно). Локально создать вручную из `.env.example`.
- **ffmpeg/ffprobe** не установлены на Windows-хосте → локальный non-docker запуск не сработает. Docker image содержит.
- **Python 3.14 + Django 5.0** — `test_health::test_unknown_endpoint_returns_404` падает в `Context.__copy__`. Это django bug, не наш код. В prod-образе python 3.12 — проходит.

---

## 10. Точки входа в код / документация

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
