# STATUS.md — текущее состояние проекта

> Снимок: **2026-04-25**, после Day 6 hardening (Person A).
> Тесты: **328 passed / 0 failed** (`test_health.py::test_unknown_endpoint_returns_404` падает только под Python 3.14 — Django/template-context bug, не наш код).

---

## 1. Хронология работ (по коммитам)

### Day 1 — Skeleton + модели + upload + Celery stub
- `dedd27e` Django/DRF skeleton + `/api/health`
- `06c66b7` модели Job/Transcript/Analysis/Artifact + initial migration
- `fa27e8e` rename app models → jobs (убрали self-shadowing)
- `ae92061` `POST /api/jobs/upload`
- `b2e2670` Celery `start_job` stub + transition helper + event publisher
- `b929385` 2 tasks от Person B (Day 1)
- `5f9a8ad` Day 1 Person B (React + Vite + Tailwind skeleton, mocks, Dropzone, useJob hook)

### Day 2 — Pipeline стадии
- `0663175` ingestion — ffmpeg normalize + ffprobe duration
- `ae70060` Whisper client + transcription pipeline + celery chain
- `5804f26` Claude client + analysis pipeline + celery chain
- `e6a5c2e` Day 2 Person B (JobPage с моками, ArtifactCard варианты, ToneSelector, copy-to-clipboard)

### Day 3 — Фан-аут артефактов
- `7eba2d3` Day 3 Person A: video clips + orchestrator + jobs API
  - ASS-subtitles генератор с word-level karaoke
  - FFmpeg pipeline для вертикальных 9:16 клипов
  - `orchestrate_artifacts` fan-out
  - `GET /api/jobs/:id` + SSE `GET /api/jobs/:id/events`
- `3957e62`, `58726c5` Day 3 Person B (text artifact workers: LinkedIn, Twitter, ShowNotes, Newsletter, YouTube Description)

### Day 4 — Интеграция
- `cbdd90f` Day 4 Person B
  - Quote graphic worker + Playwright renderer + 2 HTML шаблона (`minimal_dark`, `gradient_purple`)
  - `POST /api/artifacts/:id/regenerate` (для всех типов артефактов)
  - Расширил `orchestrate_artifacts`: + 5 text artifacts + 5 quote graphics
  - Frontend: `USE_REAL_API=true`, EventSource подключён к `/api/jobs/:id/events`, кнопка regenerate работает

### Day 6 — Hardening (Person A) + Polish (Person B)
- (uncommitted) Day 6 Person A: SoftTimeLimit handlers во всех 4 artifact workers + 4 pipeline tasks + packager → терминальное состояние вместо зависания
  - Video clip: `transient` flag в `FFmpegClipError`, retry 1× при "Invalid data" / "Connection reset" / "Server returned 5xx" / "could not seek"
  - URL ingestion: `OSError(ENOSPC)` → `STORAGE_FULL` (отдельный код от `URL_YTDLP_FAILED`)
  - `job_id` прокинут в логи `normalize_to_wav` / `probe_duration_sec` / `download_from_url` / `build_vertical_clip`
  - +20 тестов в `tests/test_hardening_day6.py` (все edge cases SPEC §5.5 и §9.5)
- `138ba16` Day 6 Person B: Toaster (pub/sub через window event), accessibility-проходка (aria-label/role/semantic html), skeleton loaders, useReducer-рефакторинг useJob, mobile-responsive

### Day 5 — URL ingestion + Packaging
- `b2e5373` Day 5 Person A
  - yt-dlp URL ingestion: `pipeline/url_ingestion.py` + расширение `ingest_job` для `source_type=URL`
  - `POST /api/jobs/from_url` (whitelist YouTube; Spotify/SoundCloud → `URL_UNSUPPORTED_HOST`)
  - `workers/packager.py` — ZIP с `clips/`, `text/`, `graphics/`, `index.txt`; partial packaging при failed артефактах
  - `check_and_trigger_packaging` хук в каждом воркере
  - Рефакторинг `orchestrate_artifacts` в две фазы (create-all-then-dispatch) — фикс для eager Celery race
  - `GET /api/jobs/:id/download` ZIP streaming (404 `PACKAGE_NOT_READY`)
  - `Job.package_path` + миграция `0002_job_package_path`
  - `package_url` в `_serialize_job` теперь реальный URL под `MEDIA_URL`

---

## 2. Архитектурные правки в Day 5 (ключевые)

| Проблема | Что было | Что стало |
|---|---|---|
| Race condition в `orchestrate_artifacts` под eager Celery: первый шустрый воркер триггерил packaging до того, как остальные артефакты создались в DB | Создание + dispatch в одном цикле по типам | Фаза 1: создать все Artifact rows; Фаза 2: диспатчить |
| `package_url` всегда `null` | Заглушка, ждали Day 5 | Берётся из `Job.package_path`, склеивается с `MEDIA_URL` |
| Job stuck в GENERATING после успеха всех артефактов | Не было перехода в COMPLETED | `check_and_trigger_packaging` → `package_job` → COMPLETED + SSE `completed` |

---

## 3. Текущее покрытие тестами

| Модуль | Файл | Кол-во |
|---|---|---|
| Errors | `test_errors.py` | 8 |
| Health | `test_health.py` | 3 |
| Settings boot | `test_settings_boot.py` | 4 |
| Models | `test_models.py` | 16 |
| Upload | `test_upload.py` | 14 |
| URL ingestion | `test_url_ingestion.py` | 18 |
| Ingestion | `test_ingestion.py` | 16 |
| Whisper | `test_whisper_client.py` | 6 |
| Transcription | `test_transcription.py` | 16 |
| Claude | `test_claude_client.py` | 6 |
| Analysis | `test_analysis.py` | 20 |
| ASS subtitles | `test_ass_subtitles.py` | 17 |
| FFmpeg clip | `test_ffmpeg_clip.py` | 10 |
| Video worker | `test_video_clip_worker.py` | 7 |
| Text artifacts | `test_text_artifacts.py` | 39 |
| Quote graphic | `test_quote_graphic_worker.py` | 17 |
| Workers/orchestrator | `test_workers.py` | 25 |
| Jobs API | `test_jobs_api.py` | 14 |
| Regenerate | `test_regenerate.py` | 17 |
| Packager | `test_packager.py` | 13 |
| Download | `test_download.py` | 11 |
| Events | `test_events.py` | 4 |
| Pipeline integration | `test_pipeline_integration.py` | 2 |
| Run pipeline cmd | `test_run_pipeline_command.py` | 8 |
| **Итого** | | **311** |

---

## 4. Открытые правки (можно сделать сейчас)

### Критическое
- [ ] **`docker-compose.yml` — celery worker без `-Q`**
  Сейчас: `celery -A core worker --loglevel=info --concurrency=4`
  Нужно: `celery -A core worker --loglevel=info --concurrency=4 -Q default,video,text_artifacts,graphics`
  Иначе задачи в очередях `video`/`text_artifacts`/`graphics` не подхватываются. В тестах не вылезает (eager mode).

### Удобные
- [ ] **`.gitignore` — добавить `.claude/` и `.coverage`** (висят как untracked постоянно)
- [ ] **`frontend/src/pages/LandingPage.jsx:6,11` — реальные POST'ы**
  Сейчас `console.log` и TODO. Нужно `fetch('/api/jobs/upload', {method, body: FormData})` и `fetch('/api/jobs/from_url', {method, body: JSON})`, потом `useNavigate('/jobs/' + jobId)`. Зона **Person B**.
- [ ] **`.env` локальный** — не создан. Скопировать `.env.example` → `.env`, вписать API-ключи.
- [ ] **ffmpeg на хосте** (опционально, только если запускаешь без Docker на Windows)

---

## 5. Что осталось по плану (`DIVISION_OF_WORK.md`)

### Day 5 (joint) — на проверку Person B
- [ ] Person B верифицирует packaging: содержимое `index.txt`, корректность папок `clips/text/graphics/`, размер ZIP

### Day 6 — Hardening (Person A) — done
- [x] SPEC §2.5: STORAGE_FULL отделён от URL_YTDLP_FAILED при ENOSPC; остальное было покрыто (live stream / geo-блок / битые метаданные / partial write / 0 байт)
- [x] SPEC §3.5: было покрыто целиком (empty / unsupported language / noise / whisper service down)
- [x] SPEC §4.5: было покрыто целиком (3-step retry с corrective msg + schema validation + dedupe overlapping clips)
- [x] SPEC §5.5: добавлен retry 1× на transient FFmpeg failure ("Invalid data", "Connection reset", "Server returned 5xx", "could not seek"); audio-only / fonts / no-subs window — было
- [x] SPEC §9.5: SoftTimeLimitExceeded handler во всех воркерах + orchestrator + packager → терминальное состояние вместо зависания в PROCESSING/GENERATING; orchestrate-timeout сливает stale QUEUED артефакты в FAILED
- [x] Retry: ffmpeg transient — 1× через self.retry; transcription/claude — внутри своих клиентов (4 retry с backoff)
- [x] `soft_time_limit=300` стоит везде (был); добавлены handler-ы — раньше лимит срабатывал, но артефакт оставался стучать в PROCESSING
- [x] `job_id` прокинут в логи `normalize_to_wav` / `probe_duration_sec` / `download_from_url` / `build_vertical_clip` — теперь `grep job_id=<uuid>` ловит ВСЁ

### Day 6 — Polish (Person B)
- [ ] Edge cases SPEC §10.6 (reconnect SSE, failed artifacts UI, retry button)
- [ ] Mobile-responsive (минимально не ломается на iPhone)
- [ ] Accessibility: `aria-label` на кнопках
- [ ] Visual polish: skeleton loaders, toast notifications

### Day 7 — Demo (оба)
- [ ] **Person A**: прогон 3–5 реальных подкастов, выбрать лучший cherry-pick для демо
- [ ] **Person A**: load test 3 параллельных upload в одном окне
- [ ] **Person A**: pre-cached demo job на случай wifi-сбоя
- [ ] **Person B**: записать 90-сек видео-демо для pitch-деки
- [ ] **Person B**: pitch deck (5 слайдов: Problem / Solution / Live Demo / Architecture / Ask)
- [ ] Совместный rehearsal демо-сценария (дважды подряд)

---

## 6. Что нужно от тебя (внешнее)

| Что | Зачем | Где |
|---|---|---|
| **`OPENAI_API_KEY`** | Whisper транскрипция (`src/services/whisper_client.py`) | `.env` файл в корне |
| **`ANTHROPIC_API_KEY`** | Claude analysis + все text artifacts | `.env` файл в корне |
| Запустить `docker compose up -d db redis` хоть раз | Проверить что Postgres + Redis поднимаются | локально |
| `docker compose run --rm app python manage.py migrate` | Применить миграции `0001_initial` + `0002_job_package_path` | локально, при первом запуске |
| Решение по `-Q` в compose | См. §4 «Критическое» | подтвердить и я закоммичу |
| Один реальный подкаст для интеграционного теста | Прогнать full pipeline upload→ZIP вручную | mp3/mp4, 5–10 минут |

---

## 7. Известные ограничения / технический долг

- **yt-dlp**: только YouTube. Spotify/SoundCloud в MVP отключены. Открытие — Day post-MVP.
- **Spotify URL** дают `URL_UNSUPPORTED_HOST`, не пробуют yt-dlp плагин — намеренно.
- **Регенерация video clip** через `POST /api/artifacts/:id/regenerate` бампает `version` и поднимает worker, но **не** удаляет старый файл с диска — накапливается. Day 6 cleanup можно добавить.
- **Packaging idempotency**: повторный `package_job` на COMPLETED — no-op (правильно), но если изменилось содержимое (regenerate после COMPLETED) — ZIP не пересобирается. По SPEC так и задумано: regenerate отдаёт превью, не пересобирает пак.
- **`completed_at`** заполняется только в `package_job` при успехе. На FAILED jobs — остаётся `null`. Не баг, но если для UI важно "когда завершён" — учесть.
- **`.env`** не в репо (правильно), но и нет локально на машине пользователя — нужно создать вручную.
- **ffmpeg/ffprobe** не установлены на Windows-хосте → локальный non-docker запуск не сработает. Docker image содержит.

---

## 8. Контакты / точки входа

- Главный спецификационный документ: `docs/SPEC.md`
- Разделение работ: `docs/DIVISION_OF_WORK.md`
- Идея проекта: `docs/PROJECT_IDEA.md`
- Правила Claude Code: `CLAUDE.md` + `.claude/rules/`

API endpoints (все смонтированы под `/api/`):

| Метод + путь | Назначение | Day |
|---|---|---|
| `GET /api/health` | Healthcheck | 1 |
| `POST /api/jobs/upload` | Загрузка файла → Job | 1 |
| `POST /api/jobs/from_url` | YouTube URL → Job | 5 |
| `GET /api/jobs/:id` | Полное состояние Job | 4 |
| `GET /api/jobs/:id/events` | SSE поток | 4 |
| `GET /api/jobs/:id/download` | ZIP пакет | 5 |
| `POST /api/artifacts/:id/regenerate` | Перегенерация артефакта | 4 |
