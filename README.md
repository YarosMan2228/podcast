# Podcast → Full Content Pack

Загружаешь один аудио/видео-файл подкаста (или ссылку на YouTube/Spotify) — получаешь на выходе пакет готового контента для публикации на 5+ платформ: вертикальные клипы, LinkedIn-пост, Twitter-тред, show notes, newsletter, квоут-графику, YouTube-описание с таймкодами.

**Хакатон-MVP**: 7 дней, 2 разработчика, демо на ~3 минуты от загрузки до результата.

## Быстрый старт

```bash
git clone <repo>
cd podcast-pack
cp .env.example .env  # заполни ключи API
docker compose up -d  # поднимет postgres + redis + app
python manage.py migrate
python manage.py runserver
# отдельный терминал:
celery -A core worker --loglevel=info --concurrency=4
```

## Документация проекта

Проект построен по **Spec-First методологии** — весь контекст для AI-агентов и разработчиков лежит в документах, а не в головах.

| Документ | Слой | Назначение |
|---|---|---|
| `docs/PROJECT_IDEA.md` | 1 | Идея, проблема, архитектура, монетизация |
| `docs/SPEC.md` | 2 | Полная техническая спецификация модулей |
| `docs/DIVISION_OF_WORK.md` | — | Разделение работы между Person A и Person B |
| `docs/SPEC_GENERATOR_PROMPT.md` | 3 | Промпт для генерации конфигурации |
| `CLAUDE.md` | 4 | Главный конфиг для Claude Code |
| `.claude/agents/*.md` | 4 | Субагенты |
| `.claude/rules/*.md` | 4 | Контекстные правила |
| `.claude/skills/*.md` | 4 | Навыки для повторяющихся задач |
| `SPEC_TEMPLATE.md` | — | Шаблон описания новой фичи |

## Разделение работы

- **Person A — Backend & Pipeline**: ingestion, транскрипция, LLM-анализ, генерация клипов, Celery-воркеры
- **Person B — Frontend & Text Artifacts**: React UI, генерация текстовых артефактов (LinkedIn/Twitter/Newsletter/ShowNotes), quote-графика, упаковка и скачивание

Подробнее — в `docs/DIVISION_OF_WORK.md`.

## Стек

- Python 3.12, Django 5, Django REST Framework
- Celery + Redis (async jobs)
- PostgreSQL 16 (job state + artifacts metadata)
- FFmpeg (видео-пайплайн)
- OpenAI Whisper API (транскрипция)
- Anthropic Claude API (семантический анализ + текстовые артефакты)
- React 18 + Tailwind (фронтенд)
- S3-совместимое хранилище (локальный volume в MVP)

## Лицензия

MIT (для хакатона).
