# SPEC.md — Техническая спецификация Podcast → Full Content Pack

> **Слой 2 Spec-First методологии.** Детальный чертёж для автономной реализации.
> Формат каждого модуля: **User Stories + Модель данных + API + UI + Бизнес-логика + Крайние случаи + Dependencies & Contracts.**
> Не содержит TODO-заглушек. Каждый тип — как в SQL/Python/TypeScript (в нашем случае JS). Assumption-ы из PROJECT_IDEA.md разрешены в конкретные решения.

---

## Содержание

1. [Глобальные контракты и типы](#1-глобальные-контракты-и-типы)
2. [Модуль: Ingestion](#2-модуль-ingestion)
3. [Модуль: Transcription](#3-модуль-transcription)
4. [Модуль: Analysis](#4-модуль-analysis)
5. [Модуль: Video Clips](#5-модуль-video-clips)
6. [Модуль: Text Artifacts](#6-модуль-text-artifacts)
7. [Модуль: Quote Graphics](#7-модуль-quote-graphics)
8. [Модуль: Packaging](#8-модуль-packaging)
9. [Модуль: Job Orchestration & API](#9-модуль-job-orchestration--api)
10. [Модуль: Frontend](#10-модуль-frontend)
11. [Межмодульная матрица зависимостей](#11-межмодульная-матрица-зависимостей)

---

## 1. Глобальные контракты и типы

Эти типы используются несколькими модулями. Определяются один раз здесь, на них ссылаются все остальные разделы.

### 1.1. Статусы Job

```
JobStatus (enum):
  PENDING       — создан, ещё не начат
  INGESTING     — идёт загрузка/нормализация
  TRANSCRIBING  — идёт Whisper API
  ANALYZING     — идёт Claude semantic analysis
  GENERATING    — fan-out артефактов в работе
  PACKAGING     — упаковка в ZIP
  COMPLETED     — всё готово
  FAILED        — ошибка, смотри поле error
```

Переходы:
```
PENDING → INGESTING → TRANSCRIBING → ANALYZING → GENERATING → PACKAGING → COMPLETED
                                                     ↓
                                                  FAILED (из любого шага)
```

### 1.2. Типы артефактов

```
ArtifactType (enum):
  VIDEO_CLIP          — mp4 9:16, 30–60 сек
  LINKEDIN_POST       — markdown 300–500 слов
  TWITTER_THREAD      — JSON {tweets: string[]}
  SHOW_NOTES          — markdown с таймкодами
  NEWSLETTER          — markdown 400 слов
  QUOTE_GRAPHIC       — png 1080x1080
  EPISODE_THUMBNAIL   — png 1280x720
  YOUTUBE_DESCRIPTION — plain text с таймкодами
```

### 1.3. Статусы Artifact

```
ArtifactStatus (enum):
  QUEUED      — ждёт воркера
  PROCESSING  — воркер работает
  READY       — готов, можно скачать
  FAILED      — ошибка, можно regenerate
```

### 1.4. Transcript JSON schema

```json
{
  "language": "en",
  "full_text": "Welcome to the show, today we talk about...",
  "duration_sec": 3612.5,
  "segments": [
    {
      "id": 0,
      "start_ms": 0,
      "end_ms": 4820,
      "text": "Welcome to the show, today we talk about AI.",
      "words": [
        {"w": "Welcome", "start_ms": 0, "end_ms": 540},
        {"w": "to", "start_ms": 540, "end_ms": 680},
        {"w": "the", "start_ms": 680, "end_ms": 820},
        {"w": "show", "start_ms": 820, "end_ms": 1200}
      ]
    }
  ]
}
```

### 1.5. EpisodeAnalysis JSON schema

```json
{
  "episode_title": "The Hidden Cost of AI Hype",
  "hook": "Most AI startups are building on sand — here's why.",
  "guest": {
    "name": "Sarah Chen",
    "bio": "CTO at Anthropic Labs, 15 years in ML infrastructure"
  },
  "themes": ["AI infrastructure", "founder economics", "technical debt"],
  "chapters": [
    {"start_ms": 0, "end_ms": 180000, "title": "Introduction"},
    {"start_ms": 180000, "end_ms": 720000, "title": "Sarah's background"}
  ],
  "clip_candidates": [
    {
      "start_ms": 423000,
      "end_ms": 478000,
      "virality_score": 9,
      "reason": "Unexpected punchline about VC pressure, strong emotional peak",
      "hook_text": "The dirty secret of AI valuations"
    }
  ],
  "notable_quotes": [
    {"text": "You can't outrun technical debt with valuation", "speaker": "Sarah Chen", "ts_ms": 512300}
  ]
}
```

### 1.6. Обработка ошибок (единый формат)

Все API endpoints возвращают ошибки в формате:
```json
{"error": {"code": "UPLOAD_TOO_LARGE", "message": "File exceeds 500MB limit", "field": "file"}}
```

Коды ошибок модуля-специфичны, см. каждый модуль.

---

## 2. Модуль: Ingestion

**Кто владелец**: Person A (Backend/Pipeline)
**Где живёт**: `src/pipeline/ingestion.py`, `src/api/views/upload.py`

### 2.1. User Stories

- US-2.1: Как подкастер, я хочу загрузить mp3/mp4/wav файл через drag-and-drop, чтобы запустить обработку без лишних кликов.
- US-2.2: Как подкастер, я хочу вставить YouTube/Spotify URL, чтобы не скачивать файл вручную.
- US-2.3: Как подкастер, при загрузке файла > 500MB я хочу сразу увидеть ошибку, а не ждать 10 минут аплоада до отказа.
- US-2.4: Как подкастер, если мой файл в нестандартном кодеке (Opus, AAC в m4a), я хочу, чтобы система сама его нормализовала и не отказывала.
- US-2.5: Как система, я хочу сохранить оригинал для видео-клипов и нормализованную WAV-копию для транскрипции.

### 2.2. Модель данных

Таблица `jobs`:

| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK, default gen_random_uuid() |
| `status` | varchar(32) | NOT NULL, default 'PENDING', CHECK IN (enum выше) |
| `source_type` | varchar(16) | NOT NULL, CHECK IN ('file', 'url') |
| `source_url` | text | NULLABLE (только для source_type='url') |
| `original_filename` | varchar(255) | NULLABLE |
| `raw_media_path` | text | NULLABLE, путь к оригиналу на диске |
| `normalized_wav_path` | text | NULLABLE, путь к нормализованной копии |
| `duration_sec` | float | NULLABLE, заполняется после ingestion |
| `file_size_bytes` | bigint | NULLABLE |
| `mime_type` | varchar(64) | NULLABLE |
| `error` | text | NULLABLE |
| `created_at` | timestamptz | NOT NULL, default NOW() |
| `updated_at` | timestamptz | NOT NULL, default NOW() |
| `completed_at` | timestamptz | NULLABLE |

Индексы:
- `(status, created_at)` — для дашбордов
- `(created_at DESC)` — для истории

### 2.3. API

#### `POST /api/jobs/upload`
Content-Type: `multipart/form-data`
Body: `file` (binary, up to 500MB)

Success 201:
```json
{"job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "PENDING"}
```

Errors:
- `400 UPLOAD_NO_FILE` — поле `file` отсутствует
- `400 UPLOAD_INVALID_FORMAT` — не audio/*, video/*, application/ogg
- `413 UPLOAD_TOO_LARGE` — > 500MB
- `500 STORAGE_ERROR` — не удалось записать файл

#### `POST /api/jobs/from_url`
Content-Type: `application/json`
Body: `{"url": "https://www.youtube.com/watch?v=..."}`

Success 201:
```json
{"job_id": "...", "status": "PENDING"}
```

Errors:
- `400 URL_INVALID` — невалидный URL
- `400 URL_UNSUPPORTED_HOST` — хост не в whitelist (youtube.com, youtu.be, open.spotify.com, soundcloud.com)
- `422 URL_YTDLP_FAILED` — yt-dlp не смог извлечь медиа (включает сообщение yt-dlp)

### 2.4. Бизнес-логика

**Нормализация файла** (после успешной загрузки, синхронно в пределах upload request'а или в первом Celery task'е):

```
ffmpeg -i <input> -ac 1 -ar 16000 -c:a pcm_s16le <normalized.wav>
```

- Mono (`-ac 1`), 16kHz (`-ar 16000`), PCM 16-bit — оптимально для Whisper.
- Оригинал сохраняется в `raw_media_path`.
- Для URL-источника: `yt-dlp -x --audio-format mp3 --audio-quality 0 <url>` → получить mp3 → затем нормализация в WAV.

**Определение длительности**:
```
ffprobe -v error -show_entries format=duration -of csv=p=0 <file>
```
Сохраняется в `duration_sec`. Если > `MAX_EPISODE_DURATION_MIN` (180) → Job сразу FAILED с error "Episode too long".

**Whitelist хостов для URL**:
- `youtube.com`, `www.youtube.com`, `youtu.be`, `m.youtube.com`
- `open.spotify.com` (yt-dlp поддерживает через плагин, в MVP оставляем на потом — возвращаем `URL_UNSUPPORTED_HOST`)
- `soundcloud.com`

В MVP: только YouTube. Spotify — стаб с понятным сообщением.

### 2.5. Крайние случаи

| Случай | Ожидаемое поведение |
|---|---|
| Файл с расширением mp3, но реально mp4 | ffmpeg нормализует всё равно; mime определяется по содержимому, не расширению |
| Файл 0 байт | 400 с кодом `UPLOAD_EMPTY_FILE` |
| Файл с битыми метаданными (ffprobe не даёт duration) | Job сразу FAILED c `INGESTION_DURATION_UNKNOWN` |
| YouTube live stream URL | yt-dlp ошибка → 422 `URL_YTDLP_FAILED` с сообщением "This is a live stream" |
| URL за geo-блоком | yt-dlp ошибка → 422 с сообщением yt-dlp как есть |
| Параллельный upload того же файла | Каждый upload = отдельный job, дедупликация не делается в MVP |
| Отключение клиента посередине аплоада | Django откатит transaction, частичный файл удаляется в `finally` блоке |
| Занято место на диске | 500 `STORAGE_FULL`, алерт в логах |

### 2.6. Dependencies & Contracts

**Потребляет**: ничего (entry point)
**Предоставляет**: `jobs.id`, `raw_media_path`, `normalized_wav_path`, `duration_sec` для модуля Transcription
**Триггерит**: Celery task `pipeline.tasks.start_job(job_id)` после успешного ingestion

---

## 3. Модуль: Transcription

**Кто владелец**: Person A
**Где живёт**: `src/pipeline/transcription.py`, `src/services/whisper_client.py`

### 3.1. User Stories

- US-3.1: Как система, я хочу получить word-level timestamps для точной нарезки клипов с субтитрами.
- US-3.2: Как пользователь, я хочу видеть в UI прогресс "Transcribing..." пока идёт Whisper, чтобы понимать что система жива.
- US-3.3: Как система, если Whisper падает с 500/timeout, я хочу retry 2 раза с экспоненциальным backoff, прежде чем помечать Job как FAILED.
- US-3.4: Как система, я хочу сохранить полный транскрипт в БД, чтобы использовать его на следующих шагах без повторного вызова Whisper.

### 3.2. Модель данных

Таблица `transcripts`:

| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `job_id` | uuid | FK jobs.id ON DELETE CASCADE, UNIQUE |
| `language` | varchar(8) | NOT NULL (ISO code из Whisper response) |
| `full_text` | text | NOT NULL |
| `segments_json` | jsonb | NOT NULL (см. §1.4) |
| `whisper_model` | varchar(32) | NOT NULL, default 'whisper-1' |
| `duration_sec` | float | NOT NULL |
| `created_at` | timestamptz | NOT NULL, default NOW() |

Индексы:
- `job_id` UNIQUE (один транскрипт на job)

### 3.3. API

Нет публичных endpoints — модуль вызывается только внутри pipeline.

**Внутренний контракт** (Celery task):
```
pipeline.tasks.transcribe(job_id: str) -> None
```

По завершении:
- Обновляет `jobs.status` с `INGESTING` → `TRANSCRIBING` → `ANALYZING` (после успеха)
- Создаёт запись в `transcripts`
- Триггерит следующий Celery task `pipeline.tasks.analyze(job_id)`

### 3.4. Бизнес-логика

**Вызов Whisper API** (псевдокод):

```
with open(normalized_wav_path, 'rb') as f:
    response = openai.audio.transcriptions.create(
        model="whisper-1",
        file=f,
        response_format="verbose_json",
        timestamp_granularities=["word", "segment"],
        language=None,  # auto-detect
    )
```

Парсинг response в наш `Transcript` schema:
- `response.segments` → `segments_json` в нашем формате (переименовать поля, перевести секунды в миллисекунды через `* 1000`)
- `response.words` → встроить в `segments_json[i].words`
- `response.language` → `language`
- `response.duration` → `duration_sec`

**Чанкинг для длинных файлов**:
Whisper API лимит — 25MB на файл. Часовой WAV 16kHz mono ≈ 115MB → нужна разбивка.
- Использовать `pydub.AudioSegment.from_wav()` → разбивка на 10-минутные куски.
- Вызов Whisper по каждому куску отдельно.
- Сшивка: добавлять к `start_ms`/`end_ms` каждого следующего куска смещение (кумулятивное).

**Retry-стратегия**:
- На OpenAI `RateLimitError`, `APIError`, `APITimeoutError` → retry с `2^attempt` секунд (1, 2, 4, 8), макс 4 попытки.
- На `InvalidRequestError` (например, файл повреждён) → без retry, FAIL сразу.

### 3.5. Крайние случаи

| Случай | Ожидаемое поведение |
|---|---|
| Файл чище тишины | Whisper вернёт пустой `segments`, но не ошибку. Проверяем `len(full_text.strip()) == 0` → FAIL c `TRANSCRIPTION_EMPTY` |
| Язык не английский | В MVP: `language != 'en'` → Job FAILED c `TRANSCRIPTION_UNSUPPORTED_LANGUAGE`, понятное сообщение в UI |
| Крайне шумный файл | Whisper может давать галлюцинации ("thank you for watching" повторяется). Детекция: если 20%+ сегментов идентичны → FAIL `TRANSCRIPTION_LIKELY_NOISE` |
| Whisper недоступен > 5 минут после всех retries | FAIL `TRANSCRIPTION_SERVICE_DOWN`, админский алерт |
| Очень короткий файл (< 30 сек) | Проходит как обычно, но в Analysis шаге возвращаем меньше clip_candidates |

### 3.6. Dependencies & Contracts

**Потребляет**: `jobs.normalized_wav_path`, `jobs.duration_sec`
**Предоставляет**: `transcripts` record целиком для модуля Analysis и всех artifact workers
**Триггерит**: `pipeline.tasks.analyze(job_id)`
**Вызывает внешнее API**: OpenAI `audio.transcriptions.create`

---

## 4. Модуль: Analysis

**Кто владелец**: Person A
**Где живёт**: `src/pipeline/analysis.py`, `src/services/claude_client.py`

### 4.1. User Stories

- US-4.1: Как система, я хочу одним Claude-вызовом извлечь все метаданные эпизода (title, hook, clips, themes, chapters, quotes), чтобы не делать 6 отдельных запросов.
- US-4.2: Как система, я хочу получить структурированный JSON, а не свободный текст, чтобы downstream воркеры могли работать без парсинга.
- US-4.3: Как пользователь, я хочу, чтобы топ-5 clip_candidates с virality_score ≥ 7 всегда были в результате, даже если Claude хочет вернуть меньше — это гарантирует наличие материала для video workers.

### 4.2. Модель данных

Таблица `analyses`:

| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `job_id` | uuid | FK jobs.id ON DELETE CASCADE, UNIQUE |
| `episode_title` | varchar(255) | NOT NULL |
| `hook` | text | NOT NULL |
| `guest_json` | jsonb | NULLABLE |
| `themes_json` | jsonb | NOT NULL (array of strings) |
| `chapters_json` | jsonb | NOT NULL (см. §1.5) |
| `clip_candidates_json` | jsonb | NOT NULL (см. §1.5) |
| `quotes_json` | jsonb | NOT NULL |
| `claude_model` | varchar(64) | NOT NULL |
| `input_tokens` | int | NOT NULL |
| `output_tokens` | int | NOT NULL |
| `created_at` | timestamptz | NOT NULL, default NOW() |

### 4.3. API

Нет публичных endpoints.

**Внутренний контракт**:
```
pipeline.tasks.analyze(job_id: str) -> None
```

### 4.4. Бизнес-логика

**Claude prompt structure** (полный текст промпта в `src/pipeline/prompts/analysis.py`):

```
SYSTEM:
You are a podcast analyst. Given a full transcript with word-level timestamps,
extract structured metadata for cross-platform content repurposing.
Return ONLY valid JSON matching the provided schema. No preamble, no markdown fences.

USER:
<transcript_full_text>
{full_text}
</transcript_full_text>

<segments_with_timestamps>
[segment.id=0 start=0ms end=4820ms] Welcome to the show...
...
</segments_with_timestamps>

Your task:
1. Generate episode_title (max 60 chars, punchy, not generic)
2. Generate hook — one sentence that makes someone click (< 120 chars)
3. Detect guest if present: name + 1-sentence bio
4. Extract 3–5 key_themes (single-word or 2-word phrases)
5. Segment into 4–8 chapters with start_ms/end_ms aligned to segment boundaries
6. Find TOP 10 clip_candidates (30–60 sec each):
   - High emotional intensity OR surprising claim OR strong storytelling
   - Self-contained (listener understands without earlier context)
   - virality_score 1–10 (honest, not inflated)
   - Include hook_text — the catchphrase that makes it shareable
7. Extract 10–15 notable_quotes — one-liners under 200 chars

Return JSON matching:
{schema_inline}
```

**Модель**: Claude Sonnet 4.6 (`claude-sonnet-4-6`)
**max_tokens**: 8000 (запас для длинного JSON)
**temperature**: 0.3 (низкая, нам нужна структура)

**Валидация ответа**:
1. `json.loads()` — если падает, retry с темпой 0 и усиленным "return ONLY JSON" в промпте
2. Pydantic schema validation — `EpisodeAnalysisSchema`. Падает → retry с конкретной error message в user message ("previous response missing field X")
3. Проверка на `len(clip_candidates) >= 5 with virality_score >= 6`. Если меньше → retry с инструкцией "be more generous with clip scores".

**Prompt caching**:
- Транскрипт идёт как `cache_control: {"type": "ephemeral"}` блок в system message.
- Переиспользуется всеми downstream Claude-вызовами (text artifacts), если они происходят в пределах 5 минут — экономия до 90% input-токенов.

### 4.5. Крайние случаи

| Случай | Ожидаемое поведение |
|---|---|
| Claude вернул не-JSON (начал с "Sure, here's...") | Retry с усиленным промптом, до 3 попыток. Если всё ещё — FAIL `ANALYSIS_INVALID_JSON`. |
| Все `virality_score < 5` (скучный эпизод) | Всё равно берём top-5 для clip workers, даже если скор низкий |
| `clip_candidates` пересекаются по временным диапазонам | Dedupe: оставляем с бóльшим virality_score |
| Эпизод очень короткий (< 5 мин), нет места для 10 clip candidates | Возвращаем сколько получилось, минимум 2 |
| Транскрипт обрывается (Whisper дал не всё) | Analysis работает с тем что есть, в логах warning |

### 4.6. Dependencies & Contracts

**Потребляет**: `transcripts.segments_json`, `transcripts.full_text`
**Предоставляет**: `analyses` record для всех artifact workers (через FK)
**Триггерит**: `workers.orchestrate_artifacts(job_id)` — fan-out на все worker tasks

---

## 5. Модуль: Video Clips

**Кто владелец**: Person A
**Где живёт**: `src/workers/video_clip_worker.py`, `src/pipeline/ffmpeg_clip.py`

### 5.1. User Stories

- US-5.1: Как подкастер, я хочу получить 5 вертикальных клипов 30–60 сек с burned-in captions, чтобы публиковать в TikTok/Reels/Shorts без редактуры.
- US-5.2: Как подкастер, я хочу, чтобы текущее слово в субтитрах было выделено цветом (karaoke-style), чтобы клипы выглядели профессионально.
- US-5.3: Как подкастер, если один клип получился плохим, я хочу кликнуть "regenerate" и получить альтернативный 30–60 сек фрагмент из top-10 кандидатов.
- US-5.4: Как система, я хочу генерировать клипы параллельно (до 5 одновременно), чтобы уложиться в ~3 минуты общего времени.

### 5.2. Модель данных

Запись в таблице `artifacts`:

| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `job_id` | uuid | FK jobs.id ON DELETE CASCADE |
| `type` | varchar(32) | NOT NULL, в нашем случае 'VIDEO_CLIP' |
| `status` | varchar(16) | NOT NULL, enum ArtifactStatus |
| `index` | int | NOT NULL, порядковый номер 0..4 |
| `file_path` | text | NULLABLE, относительный путь к mp4 |
| `text_content` | text | NULLABLE (не для видео) |
| `metadata_json` | jsonb | NOT NULL, см. ниже |
| `version` | int | NOT NULL, default 1 (инкрементируется при regenerate) |
| `error` | text | NULLABLE |
| `created_at` | timestamptz | NOT NULL |
| `updated_at` | timestamptz | NOT NULL |

Индексы:
- `(job_id, type, index)` UNIQUE
- `(job_id, status)`

`metadata_json` для VIDEO_CLIP:
```json
{
  "source_clip_candidate_index": 2,
  "start_ms": 423000,
  "end_ms": 478000,
  "duration_sec": 55.0,
  "hook_text": "The dirty secret of AI valuations",
  "virality_score": 9,
  "file_size_bytes": 8421345,
  "resolution": "1080x1920",
  "captions_style": "karaoke_white_yellow"
}
```

### 5.3. API

**Публичный endpoint**:

#### `POST /api/artifacts/{artifact_id}/regenerate`
Body: пусто (для видео) или `{"variation_hint": "more energetic opener"}` (опционально)

Success 202:
```json
{"artifact_id": "...", "status": "QUEUED", "version": 2}
```

**Внутренний контракт**:
```
workers.tasks.generate_video_clip(artifact_id: str, regenerate: bool = False) -> None
```

### 5.4. Бизнес-логика

**Выбор фрагмента**:
- Первоначальная генерация: берём `clip_candidates[artifact.index]` (ordered by virality_score DESC).
- Regenerate: берём следующий неиспользованный candidate из `clip_candidates` (track в `metadata_json.used_candidate_indices`).
- Если кандидаты кончились → возврат того же с другими параметрами (случайное смещение ±3 сек от центра).

**FFmpeg pipeline** (шаги выполняются одной `ffmpeg` командой через `-filter_complex`):

1. **Cut segment** с 1-секундным head/tail padding: `-ss (start_ms/1000 - 1)` `-t (duration_sec + 2)`
2. **Scale + pad to 9:16**:
   - Input: 16:9 1920x1080 (стандарт YouTube)
   - Scale: `scale=w=1080:h=1920:force_original_aspect_ratio=decrease`
   - Pad: `pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black` (в MVP — чёрные полосы сверху/снизу). Либо `crop=1080:1920` для center-crop (выбираем через ENV флаг).
   - Для audio-only эпизодов: генерируем видео из waveform — `ffmpeg -i audio.wav -filter_complex "[0:a]showwaves=s=1080x1080:mode=cline:colors=white" -c:v libx264 clip.mp4`
3. **Burn captions** через subtitles filter с ASS-файлом:
   - Генерируем `.ass` из word-timestamps этого сегмента
   - Стиль ASS: `Fontname=Inter,Fontsize=72,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=4,Alignment=2,MarginV=200`
   - Word-highlight через `\k` теги в ASS карооке-формат: `{\k{duration_cs}}Word `
4. **Encode**: `-c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart`
5. **Output**: `artifacts/{job_id}/clip_{index}_v{version}.mp4`

**ASS subtitle generation** (pseudocode):
```python
def build_ass(words, clip_start_ms, clip_end_ms):
    # Группируем слова в "фразы" по 3–5 слов или по паузам > 300ms
    # Каждая фраза = один Dialogue event в ASS
    # Внутри фразы: {\k{word_duration_cs}} перед каждым словом для karaoke-подсветки
    ...
```

**Параллелизм**: все 5 клипов — отдельные Celery tasks в очереди `video_clips` (concurrency=2 на одном воркере, чтобы не задушить CPU).

### 5.5. Крайние случаи

| Случай | Ожидаемое поведение |
|---|---|
| `clip_candidate` выходит за границы аудио (end_ms > duration) | Обрезаем до `min(end_ms, duration_ms)`; если duration становится < 20 сек → пропускаем этот candidate, берём следующий |
| FFmpeg падает с ошибкой "Invalid data" | Retry 1 раз; если снова → artifact FAILED с error из ffmpeg stderr |
| Слов нет (транскрипт пустой в этом диапазоне) | Рендерим клип без субтитров; логируем warning |
| Исходник — audio-only (mp3) | Генерируем waveform-видео (showwaves filter) + title overlay + subtitles |
| Fonts отсутствуют | Fallback на DejaVu Sans (включен в Dockerfile) |
| `regenerate` вызван, но все candidates использованы | 409 `NO_MORE_VARIATIONS` в ответе endpoint |

### 5.6. Dependencies & Contracts

**Потребляет**: `analyses.clip_candidates_json`, `transcripts.segments_json`, `jobs.raw_media_path`
**Предоставляет**: `.mp4` файлы в `{MEDIA_ROOT}/artifacts/{job_id}/clip_*.mp4`
**Триггерит**: обновление `artifacts.status = READY`, SSE event на frontend

---

## 6. Модуль: Text Artifacts

**Кто владелец**: Person B (Frontend/Artifacts)
**Где живёт**: `src/workers/text_artifact_worker.py`, `src/pipeline/prompts/text_artifacts.py`

### 6.1. User Stories

- US-6.1: Как подкастер, я хочу получить LinkedIn-пост на 300–500 слов в аналитическом тоне, готовый к публикации.
- US-6.2: Как подкастер, я хочу получить Twitter-тред из 6–10 твитов, каждый ≤ 280 символов, с сильным первым твитом.
- US-6.3: Как подкастер, я хочу получить show notes с: guest bio, списком тем, таймкодами, упомянутыми ссылками/книгами/инструментами — в Markdown.
- US-6.4: Как подкастер, я хочу newsletter 400 слов с hook, 3 takeaways и CTA — готовый отправить в Substack.
- US-6.5: Как подкастер, я хочу YouTube description с SEO-тегами, таймкодами и chapters — готовый вставить в YouTube Studio.
- US-6.6: Как подкастер, для любого текстового артефакта я хочу кнопку "regenerate with different tone" (варианты: more analytical / more casual / more punchy).

### 6.2. Модель данных

Используется общая таблица `artifacts` (см. §5.2), `text_content` заполнен, `file_path` NULL.

`metadata_json` для текстовых артефактов:
```json
{
  "tone": "analytical",
  "word_count": 432,
  "claude_model": "claude-sonnet-4-6",
  "input_tokens": 15234,
  "output_tokens": 892,
  "tweet_count": 8
}
```

### 6.3. API

**Публичный endpoint** (общий с video clip):

#### `POST /api/artifacts/{artifact_id}/regenerate`
Body: `{"tone": "more_casual"}` или `{}`

Разрешённые tone для text artifacts:
- `analytical` (default для LinkedIn, Show Notes)
- `casual` (default для Twitter, Newsletter)
- `punchy` (опция для всех)
- `professional` (опция для всех)

**Внутренний контракт**:
```
workers.tasks.generate_text_artifact(artifact_id: str, tone: str | None) -> None
```

### 6.4. Бизнес-логика

**Единый подход**: каждый тип артефакта = отдельный промпт-шаблон в `src/pipeline/prompts/text_artifacts.py`. Все промпты принимают `{transcript_full_text, analysis, tone}`.

**Структура промпта для LinkedIn** (пример, остальные по аналогии):

```
SYSTEM:
You are an expert LinkedIn content writer specializing in podcast repurposing.
Your voice: thoughtful analysis without corporate jargon. First person where it fits the host.

USER:
<transcript>{full_text}</transcript>
<analysis>{themes, hook, key_quotes}</analysis>
<tone>{tone}</tone>

Write a LinkedIn post that:
- Opens with a hook (question, surprising claim, or contrarian take) in the first 2 lines
- 300–500 words total
- Uses short paragraphs (2–3 sentences each) with line breaks between them
- Ends with either: (a) a question to drive engagement, or (b) a clear CTA to listen
- No hashtags in the body; append 3–5 relevant hashtags at the very end
- Do NOT use AI-detection markers: no "In conclusion", no "It's important to note", no bullet points.

Return ONLY the post text. No preamble, no markdown fences.
```

**Twitter thread prompt** (особенности):
- Каждый твит ≤ 270 chars (10 запаса на случай счёта emoji)
- Первый твит = strongest hook, должен работать без остальных
- Последний твит = "🧵 End" или CTA с ссылкой placeholder `{{EPISODE_URL}}`
- Return JSON `{"tweets": ["...", "..."]}` для точной валидации длин

**Show notes** возвращает Markdown со структурой:
```
# {episode_title}
> {hook}

## About the guest
{guest_bio}

## Topics covered
- {theme 1}
- {theme 2}

## Timestamps
- [{hh:mm:ss}] {chapter_title}

## Notable quotes
> "{quote}" — {speaker}

## Links mentioned
- (extracted by Claude if any URLs/books/tools mentioned in transcript)
```

**Newsletter**:
- Подготовлен под Substack-форматирование
- Структура: [subject_line] + [hook paragraph] + [3 takeaways as headings] + [CTA to full episode]

**YouTube description**:
- Первые 150 chars = hook (critical for SEO preview)
- Затем таймкоды формата `MM:SS - Chapter Title` (YouTube парсит это автоматом для chapters)
- Затем 3–5 keywords секция
- Placeholder для социальных ссылок: `{{PODCAST_LINKS}}`

**Параллелизм**: все text artifacts — отдельные Celery tasks, очередь `text_artifacts` (concurrency=6, LLM-вызовы I/O-bound).

### 6.5. Крайние случаи

| Случай | Ожидаемое поведение |
|---|---|
| Claude пишет > 500 слов для LinkedIn | Retry с усиленным "max 500 words" в промпте. Если опять → обрезать программно по последнему предложению < 500 слов |
| Twitter-тред: один твит > 280 chars | Retry с tweet count fix. Если снова → сплит твита программно по последнему пробелу перед 270 |
| Newsletter: Claude вернул markdown с кодовыми фенсами | Strip ``` с обеих сторон перед сохранением |
| Show notes: нет guest info в транскрипте | Секция "About the guest" пропускается (не рендерится) |
| YouTube description: нет chapters | Секция таймкодов пропускается |
| Пользователь жмёт regenerate 5 раз подряд | Rate limit на endpoint: 3 regenerate одного artifact за минуту, 429 с `Retry-After` |

### 6.6. Dependencies & Contracts

**Потребляет**: `transcripts.full_text`, `analyses.*`
**Предоставляет**: `artifacts.text_content` для frontend отображения
**Использует prompt caching**: транскрипт в system message с `cache_control` — все 5 text-воркеров переиспользуют один кэш

---

## 7. Модуль: Quote Graphics

**Кто владелец**: Person B
**Где живёт**: `src/workers/quote_graphic_worker.py`, `frontend/src/quote_templates/` (HTML-шаблоны)

### 7.1. User Stories

- US-7.1: Как подкастер, я хочу получить 5 quote-графиков PNG 1080x1080 с цитатами из эпизода, чтобы постить в Instagram.
- US-7.2: Как подкастер, я хочу, чтобы графика выглядела консистентно (одна цветовая схема, типографика), а не как 5 разных мемов.
- US-7.3: Как подкастер, я хочу указать свой логотип один раз (в profile settings) и видеть его на всех quote-графиках — **в MVP это захардкожено, в v2 станет настройкой**.

### 7.2. Модель данных

`artifacts` с `type=QUOTE_GRAPHIC`, `file_path` указывает на PNG, `metadata_json`:
```json
{
  "quote_text": "You can't outrun technical debt with valuation",
  "speaker": "Sarah Chen",
  "template_id": "minimal_dark",
  "source_quote_index": 2
}
```

### 7.3. Бизнес-логика

**Выбор цитат**: берём top-5 из `analyses.quotes_json`, отсортированных по длине (не слишком короткие < 20 chars и не слишком длинные > 180 chars).

**Рендеринг через Playwright** (HTML → PNG):

1. HTML-шаблон в `frontend/src/quote_templates/minimal_dark.html`:
```html
<!DOCTYPE html>
<html>
<head>
  <style>
    body { margin:0; width:1080px; height:1080px; background:#0a0a0a;
           font-family:'Inter',sans-serif; display:flex; flex-direction:column;
           justify-content:center; padding:80px; box-sizing:border-box; }
    .quote { color:#fff; font-size:56px; line-height:1.25; font-weight:600; }
    .author { color:#888; font-size:28px; margin-top:48px; }
    .brand  { position:absolute; bottom:48px; left:80px; color:#555; font-size:20px; }
  </style>
</head>
<body>
  <div class="quote">"{{QUOTE}}"</div>
  <div class="author">— {{SPEAKER}}</div>
  <div class="brand">{{PODCAST_NAME}}</div>
</body>
</html>
```

2. Python код:
```python
async def render_quote(quote, speaker, output_path):
    html = template.replace("{{QUOTE}}", escape(quote))\
                   .replace("{{SPEAKER}}", escape(speaker))\
                   .replace("{{PODCAST_NAME}}", "Podcast Pack Demo")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width":1080,"height":1080})
        await page.set_content(html)
        await page.screenshot(path=output_path, omit_background=False)
        await browser.close()
```

3. Типографика авторасчёт: если quote > 120 символов → шаблон с меньшим font-size (48px вместо 56px).

### 7.4. Крайние случаи

| Случай | Ожидаемое поведение |
|---|---|
| Цитата содержит emoji | Работает, Chromium рендерит их корректно |
| Цитата с длинными словами (URLs) | CSS `word-break: break-word` для предотвращения overflow |
| Всего < 5 notable_quotes в analysis | Рендерим сколько есть, минимум 2 |
| Playwright timeout (> 30 сек) | FAIL артефакта; обычно indicator больших системных проблем |

### 7.5. Dependencies & Contracts

**Потребляет**: `analyses.quotes_json`
**Предоставляет**: PNG в `{MEDIA_ROOT}/artifacts/{job_id}/quote_{index}.png`

---

## 8. Модуль: Packaging

**Кто владелец**: Person B
**Где живёт**: `src/workers/packager.py`

### 8.1. User Stories

- US-8.1: Как подкастер, я хочу одной кнопкой скачать ZIP со всеми артефактами, чтобы не кликать 20 раз.
- US-8.2: Как подкастер, я хочу, чтобы ZIP был организован по папкам: `clips/`, `text/`, `graphics/` — чтобы сразу понимать содержимое.
- US-8.3: Как подкастер, я хочу `index.txt` внутри ZIP с описанием что где и как использовать.

### 8.2. Бизнес-логика

Структура ZIP:
```
podcast_pack_{job_id}_{timestamp}.zip
├── index.txt                  # оглавление + инструкции
├── clips/
│   ├── clip_1_hook.mp4
│   ├── clip_2.mp4
│   └── ...
├── text/
│   ├── linkedin.md
│   ├── twitter_thread.md      # каждый твит на отдельной строке с разделителем
│   ├── show_notes.md
│   ├── newsletter.md
│   └── youtube_description.txt
└── graphics/
    ├── thumbnail.png
    ├── quote_1.png
    └── ...
```

Вызывается одним Celery task'ом после того, как **все** артефакты job'а в статусе `READY`:
```
workers.tasks.package_job(job_id: str) -> None
```

При завершении: `jobs.status = COMPLETED`, путь к ZIP в отдельном поле `jobs.package_path`.

### 8.3. Dependencies & Contracts

**Потребляет**: все `artifacts` для `job_id`
**Предоставляет**: ZIP-файл для endpoint `GET /api/jobs/{job_id}/download`

---

## 9. Модуль: Job Orchestration & API

**Кто владелец**: Person A (API), Person B (SSE consumption)
**Где живёт**: `src/api/views/jobs.py`, `src/workers/tasks.py` (оркестратор)

### 9.1. User Stories

- US-9.1: Как frontend, я хочу получать SSE-поток событий "artifact ready", чтобы обновлять UI в реальном времени.
- US-9.2: Как frontend, я хочу за один GET запрос получить полное состояние job'а со всеми артефактами.
- US-9.3: Как система, я хочу триггерить packaging только когда **все** артефакты READY.

### 9.2. Модель данных

Уже описана в §2.2 (jobs) и §5.2 (artifacts). Без дополнительных таблиц.

### 9.3. API

#### `GET /api/jobs/{job_id}`
Success 200:
```json
{
  "job_id": "...",
  "status": "GENERATING",
  "progress": {
    "total_artifacts": 9,
    "ready": 3,
    "processing": 4,
    "queued": 2,
    "failed": 0
  },
  "analysis": {
    "episode_title": "The Hidden Cost of AI Hype",
    "hook": "Most AI startups are building on sand — here's why."
  },
  "artifacts": [
    {
      "id": "...",
      "type": "VIDEO_CLIP",
      "index": 0,
      "status": "READY",
      "file_url": "/media/artifacts/{job_id}/clip_0_v1.mp4",
      "text_content": null,
      "metadata": {"virality_score": 9, "duration_sec": 55.0},
      "version": 1
    }
  ],
  "package_url": null,
  "error": null
}
```

Error:
- `404 JOB_NOT_FOUND`

#### `GET /api/jobs/{job_id}/events` (SSE)
Response: `text/event-stream`

Events:
```
event: status_changed
data: {"status": "ANALYZING"}

event: artifact_ready
data: {"artifact_id": "...", "type": "LINKEDIN_POST", "index": 0}

event: artifact_failed
data: {"artifact_id": "...", "error": "Claude returned invalid JSON"}

event: completed
data: {"package_url": "/media/packages/podcast_pack_....zip"}
```

Клиент переподключается сам через EventSource API. Сервер отправляет `: keepalive\n\n` каждые 15 сек.

#### `GET /api/jobs/{job_id}/download`
Response: `application/zip` stream

Errors:
- `404 PACKAGE_NOT_READY` — если `jobs.status != COMPLETED`

### 9.4. Бизнес-логика оркестратора

Celery task chain:
```python
# После ingestion:
transcribe.apply_async(args=[job_id])
  └→ on success: analyze.apply_async(args=[job_id])
        └→ on success: orchestrate_artifacts(job_id)

def orchestrate_artifacts(job_id):
    """Fan-out для всех артефактов."""
    analysis = Analysis.objects.get(job_id=job_id)
    
    # Create artifact records (QUEUED)
    artifacts_to_create = [
        ("VIDEO_CLIP", i) for i in range(5)
    ] + [
        ("LINKEDIN_POST", 0),
        ("TWITTER_THREAD", 0),
        ("SHOW_NOTES", 0),
        ("YOUTUBE_DESCRIPTION", 0),
        # если ENABLE_NEWSLETTER: + newsletter
        # если ENABLE_QUOTES: + quote_graphic × 5
        # если ENABLE_THUMBNAIL: + thumbnail
    ]
    
    for type_, index in artifacts_to_create:
        art = Artifact.objects.create(job_id=job_id, type=type_, index=index, status='QUEUED')
        if type_ == 'VIDEO_CLIP':
            generate_video_clip.apply_async(args=[art.id], queue='video')
        elif type_ in TEXT_TYPES:
            generate_text_artifact.apply_async(args=[art.id], queue='text')
        elif type_ == 'QUOTE_GRAPHIC':
            generate_quote_graphic.apply_async(args=[art.id], queue='graphics')
    
    Job.objects.filter(id=job_id).update(status='GENERATING')

# Каждый воркер в конце вызывает:
def check_and_trigger_packaging(job_id):
    if not Artifact.objects.filter(job_id=job_id).exclude(status__in=['READY','FAILED']).exists():
        package_job.apply_async(args=[job_id], queue='default')
```

SSE endpoint реализован через Django `StreamingHttpResponse` + Redis pub/sub:
- Каждый воркер при обновлении статуса артефакта делает `redis.publish(f"job:{job_id}", event_json)`
- SSE view подписывается на `job:{job_id}` канал и стримит клиенту

### 9.5. Крайние случаи

| Случай | Ожидаемое поведение |
|---|---|
| Артефакт завис в PROCESSING > 5 мин | Celery soft_time_limit=300s; таск убивается, artifact.status = FAILED |
| Клиент закрыл SSE в середине | Сервер детектит broken pipe, закрывает pub/sub подписку |
| Один artifact FAILED, остальные OK | Job всё равно переходит в COMPLETED (если хотя бы 50% READY), ZIP содержит что есть + index.txt помечает отсутствующее |

### 9.6. Dependencies & Contracts

**Потребляет**: все нижестоящие модули
**Предоставляет**: REST API + SSE stream для frontend

---

## 10. Модуль: Frontend

**Кто владелец**: Person B
**Где живёт**: `frontend/`

### 10.1. User Stories

- US-10.1: Как посетитель лендинга, я за 5 секунд понимаю что делает сервис (hero + 1 gif демо).
- US-10.2: Как пользователь, я могу drag-and-drop файл на главном экране и увидеть сразу, что запустилась обработка.
- US-10.3: Как пользователь, я вижу live-прогресс с индикатором по каждому артефакту (queued / processing / ready / failed).
- US-10.4: Как пользователь, я могу превью каждого артефакта в карточке: видео играется inline, тексты в раскрывающемся блоке.
- US-10.5: Как пользователь, я могу скопировать текст одной кнопкой (toast "Copied!") или скачать один файл.
- US-10.6: Как пользователь, я могу нажать "Regenerate" на любом артефакте и увидеть новую версию через 10–30 сек.

### 10.2. Структура экранов

**3 основных экрана + модалки:**

1. **Landing / Upload** (`/`)
   - Hero: заголовок + подзаголовок + CTA
   - Dropzone component (полноэкранный drag-over feedback)
   - Альтернативный input: paste URL
   - "How it works": 3 шага с иконками

2. **Progress** (`/jobs/{job_id}`, когда `status != COMPLETED`)
   - Наверху: название эпизода (появляется после analysis) + hook
   - Общий прогресс-бар (по этапам: ingesting/transcribing/analyzing/generating/packaging)
   - Grid of artifact-placeholder cards с статусами в реальном времени
   - При клике на готовую карточку — раскрывается превью

3. **Results** (`/jobs/{job_id}`, когда `status == COMPLETED`)
   - Сверху: "Download all (ZIP)" primary button
   - Grid of artifact cards (9–15 штук):
     - Video card: встроенный `<video>` + download + regenerate
     - Text card: preview first 200 chars + expand + copy + regenerate + tone selector
     - Graphic card: PNG preview + download + regenerate

### 10.3. Компоненты

| Компонент | Props | Состояния |
|---|---|---|
| `<Dropzone onFile={}/>`| — | idle, drag-over, uploading, error |
| `<UrlInput onSubmit={}/>` | — | empty, valid, invalid, submitting |
| `<JobProgressBar status phases={}/>` | status | renders one of 6 phases highlighted |
| `<ArtifactCard artifact onRegenerate={}/>` | artifact obj | queued, processing, ready, failed |
| `<VideoArtifact artifact/>` | artifact | плеер + download |
| `<TextArtifact artifact/>` | artifact | collapsed/expanded, copied-toast |
| `<GraphicArtifact artifact/>` | artifact | preview + download |
| `<ToneSelector value onChange={}/>` | value | dropdown {analytical, casual, punchy, professional} |

### 10.4. State management

В MVP — без Redux/Zustand. Достаточно:
- `useState` локально в каждой странице
- Custom hook `useJob(jobId)`:
  - Делает `GET /api/jobs/{jobId}` при маунте
  - Открывает EventSource на `/api/jobs/{jobId}/events`
  - Обновляет local state при каждом SSE event
  - Возвращает `{job, artifacts, isConnected}`

### 10.5. Стили

- Tailwind CSS, без component library (Material/Chakra — overkill для 3 экранов)
- Светлая тема
- Primary color: `indigo-600`; accent: `emerald-500` (для "ready" состояний); error: `red-500`
- Используется `@tailwindcss/typography` для рендера markdown внутри text artifacts

### 10.6. Крайние случаи

| Случай | Ожидаемое поведение |
|---|---|
| SSE соединение упало | Автопереподключение через 3 сек; во время — polling `GET /api/jobs/{id}` каждые 5 сек как fallback |
| Пользователь обновил страницу в середине обработки | Состояние восстанавливается полностью из `GET /api/jobs/{id}` |
| Upload прервался (сеть) | Показать ошибку и кнопку "Try again" |
| Браузер не поддерживает EventSource (IE) | В MVP игнорируем, браузеры ≥ 2022 все поддерживают |
| Артефакт FAILED | Карточка красного цвета, кнопка "Retry" (вызывает regenerate) |

### 10.7. Dependencies & Contracts

**Потребляет**: REST API из §9.3 + SSE stream из §9.3
**Предоставляет**: пользовательский UI

---

## 11. Межмодульная матрица зависимостей

Эта матрица — ground truth для понимания, какие модули блокируют друг друга при разработке. Используется в DIVISION_OF_WORK.md для планирования последовательности работ.

| Модуль | Зависит от | Блокирует |
|---|---|---|
| Ingestion | — | Transcription |
| Transcription | Ingestion | Analysis, Video Clips |
| Analysis | Transcription | все artifact workers |
| Video Clips | Analysis, raw media из Ingestion | Packaging |
| Text Artifacts | Analysis, Transcription (текст) | Packaging |
| Quote Graphics | Analysis (quotes) | Packaging |
| Packaging | все артефакты | — |
| Orchestrator (Celery tasks.py) | все воркеры | — |
| REST API | Orchestrator, модели | Frontend |
| SSE endpoint | Redis pub/sub, Orchestrator | Frontend |
| Frontend | REST API + SSE | — |

**Критический путь**: Ingestion → Transcription → Analysis → (Video Clips || Text Artifacts) → Packaging

**Параллелизм на уровне разработки**:
- Person A может параллельно: Ingestion + заглушка Transcription
- Person B может параллельно: Frontend с мок-данными (mock API responses) до готовности Analysis
- Интеграция: в Day 4–5 Person A отдаёт реальный API, Person B переключает fetch с mock на real
