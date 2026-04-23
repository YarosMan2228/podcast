# CLAUDE.md — Podcast → Full Content Pack

## Project overview

Web-приложение: пользователь загружает подкаст (audio/video file или YouTube URL) → через ~3 минуты получает пакет из 5+ артефактов для публикации: вертикальные видео-клипы с субтитрами, LinkedIn-пост, Twitter-тред, show notes, newsletter, quote-графика. **Хакатон MVP за 7 дней, двумя разработчиками, Python + React.**

## Tech stack

| Слой | Технология |
|---|---|
| Backend | Python 3.12, Django 5.0, DRF |
| Async | Celery 5.4 + Redis 7 |
| БД | PostgreSQL 16 |
| Video | FFmpeg (CLI через subprocess) |
| Транскрипция | OpenAI Whisper API (`whisper-1`) |
| LLM | Anthropic Claude (`claude-sonnet-4-6`) |
| Frontend | React 18 + Vite + Tailwind (JSX, **не TypeScript**) |
| Graphics | Pillow + Playwright (HTML→PNG) |

## Architecture (5 слоёв pipeline)

1. **Ingestion** — приём файла/URL, нормализация через ffmpeg в mono 16kHz WAV
2. **Transcription** — Whisper API с word-level timestamps, результат в `Transcript.segments_json`
3. **Analysis** — один Claude-вызов извлекает title, hook, top-10 clip candidates, themes, chapters, quotes в JSON
4. **Parallel artifact generation** — fan-out через Celery: 5 video clips + 5 text artifacts + quotes, в отдельных очередях `video`/`text`/`graphics`
5. **Packaging** — ZIP с папками `clips/`, `text/`, `graphics/`, `index.txt`

## Code conventions

- **Type hints обязательны** для всех публичных функций и методов (параметры + return)
- **Snake_case** для файлов, переменных, функций; **PascalCase** для классов; **SCREAMING_CASE** для enums
- **Никаких DB-вызовов во views** — только через сервисный слой (`src/services/`) или модели с менеджерами
- **Celery tasks** всегда с `bind=True, max_retries=3, soft_time_limit=300, acks_late=True`
- **Все внешние API вызовы** — только через клиенты в `src/services/` (`whisper_client`, `claude_client`)
- **Структурированные ошибки**: все API endpoints возвращают `{"error": {"code": "...", "message": "...", "field": "..."}}` (см. SPEC.md §1.6)
- **UUIDs как PK** для всех моделей через `uuid.uuid4`
- **Миграции** создаём согласованно — перед `makemigrations` делаем `git pull`

## Where things live

| Директория | Ответственность |
|---|---|
| `src/api/views/` | DRF views, только HTTP-слой |
| `src/pipeline/` | ingestion, transcription, analysis — обработка пайплайна |
| `src/pipeline/prompts/` | все LLM промпты как константы/функции |
| `src/workers/` | Celery tasks (по одному файлу на тип артефакта) |
| `src/services/` | Обёртки внешних API (`whisper_client`, `claude_client`) |
| `src/jobs/` | Django app с моделями (Job, Transcript, Analysis, Artifact), enums, миграции. Импорт — `from jobs.models import Job, Transcript, Analysis, Artifact`. |
| `frontend/src/components/` | Презентационные React-компоненты |
| `frontend/src/pages/` | Страницы (роутинг-level компоненты) |
| `frontend/src/hooks/` | Custom hooks, включая `useJob(jobId)` |
| `docs/` | `PROJECT_IDEA.md`, `SPEC.md`, `DIVISION_OF_WORK.md` — читать перед изменениями |

## How to run locally

```bash
cp .env.example .env           # заполнить OPENAI_API_KEY и ANTHROPIC_API_KEY
docker compose up -d db redis  # поднять Postgres и Redis
docker compose up app worker   # или локально: python manage.py runserver + celery -A core worker
# Frontend в отдельном терминале:
cd frontend && npm install && npm run dev
```

## When working in this repo

1. **Перед любым изменением** — прочитай соответствующий раздел в `docs/SPEC.md`. Он — ground truth, не додумывай своё.
2. **Перед изменением API-контракта** (endpoints, event schemas, модели `Artifact`/`Job`) — проверь, что это не блокирует второго разработчика. См. `docs/DIVISION_OF_WORK.md` матрицу зависимостей.
3. **Никогда не коммить секреты**. `.env` в `.gitignore`, используй `.env.example` для шаблонов.
4. **Не создавай новых моделей без миграции в том же коммите.** Миграция + код = один коммит.
5. **При добавлении нового типа артефакта** — следуй `.claude/skills/implement-artifact-worker.md`.
6. **Subagent discipline**: если задача в твоей специализации — работай автономно; если на стыке — явно упомяни, что нужно согласование.
