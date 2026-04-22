# PROJECT_IDEA.md — Podcast → Full Content Pack

> **Слой 1 Spec-First методологии.** Этот документ — аналитический вход для AI, который сгенерирует техническую спецификацию (SPEC.md).
> Все конкретные числа в этом файле — **assumptions** хакатонной команды, основанные на общедоступных индустрийных источниках (подкаст-статистика 2024–2025), а не на собственных интервью с пользователями. Помечать как `[ASSUMPTION]` там, где это критично.

---

## 1. Проблема

Средний независимый подкастер с аудиторией 1–20k слушателей тратит на post-production репурпозинг одного эпизода (~60 минут) **3–5 часов** [ASSUMPTION, основано на форумах Podcasters' Paradise и обсуждениях на r/podcasting]:

- Вручную искать "виральные" 30–60-секундные моменты — 40–60 минут
- Нарезать 5–7 вертикальных клипов с субтитрами (CapCut/Descript) — 60–90 минут
- Написать LinkedIn-пост в голосе хоста — 20–30 минут
- Составить Twitter-тред из 6–10 твитов — 15–25 минут
- Сгенерировать show notes с таймкодами и ссылками — 20–30 минут
- Написать newsletter-драфт — 20–40 минут
- Сделать 3–5 quote-график — 30–45 минут

Из-за этого **"content tax"** около **80% независимых подкастеров** [ASSUMPTION] публикуют только сырой эпизод и не делают репурпозинг, теряя основной канал роста аудитории (короткие клипы на TikTok/Reels/Shorts).

Существующие инструменты решают по одному срезу:

| Инструмент | Цена | Что делает | Что не делает |
|---|---|---|---|
| Opus Clip | $29/мес | Только вертикальные клипы | Без текстовых артефактов |
| Descript | $20/мес | Редактор + транскрипция | Требует ручной работы в сложном UI |
| Castmagic | $39/мес | Show notes | Нет клипов |
| Riverside | $24/мес | Запись + базовое редактирование | Не репурпозинг |

Креатор в итоге платит за 3–4 подписки (~$70–100/мес) и **всё равно тратит 1.5–2 часа** на ручную работу и стыковку артефактов между инструментами.

---

## 2. Решение

Единое веб-приложение: один upload → полный Content Pack за ~5 минут.

**Пошаговый процесс** (каждый шаг = модуль):

1. **Ingestion** — принимает mp3/mp4/wav до 500MB или URL (YouTube/Spotify), нормализует в mono 16kHz WAV для транскрипции, оригинал сохраняет для клип-генерации.
2. **Transcription** — отправляет в OpenAI Whisper API, получает JSON с word-level timestamps.
3. **Semantic Analysis** — один Claude API вызов с транскриптом: извлекает `episode_title`, `hook`, `top_10_clip_moments` (с virality_score 1–10), `key_themes[]`, `chapters[]`, `notable_quotes[]`, `guest_info`.
4. **Parallel Generation** — fan-out через Celery:
   - **Video clips** (5–7 штук) — ffmpeg: вырезать сегмент, вертикальный 9:16 crop, burned-in captions с word-level подсветкой через libass/ASS-формат
   - **LinkedIn article** — отдельный Claude вызов с tuned промптом
   - **Twitter thread** — отдельный Claude вызов
   - **YouTube description** — шаблон с таймкодами из `chapters[]`
   - **Show notes** — Markdown, отдельный Claude вызов
   - **Newsletter draft** — отдельный Claude вызов
   - **Quote graphics** (5 штук) — HTML-шаблон → Playwright → PNG
   - **Episode thumbnail** — Pillow + шаблон (AI-генерация опционально, флаг `ENABLE_AI_THUMBNAILS`)
5. **Packaging** — всё в ZIP, в UI превью-карточки с копированием в буфер, скачиванием по одному и "regenerate this artifact".

**Регенерация** — ключевой UX-паттерн. Любой артефакт пересоздаётся одним кликом за 10–30 секунд с новым seed или вариацией промпта.

---

## 3. Почему сейчас

- **Тайминг модели.** Claude Sonnet 4.6 и Opus 4.7 стабильно держат 200k контекст — полный транскрипт часового подкаста (~12–15k слов, ~20k токенов) помещается целиком в один промпт без RAG-костылей.
- **Whisper API дешёвый.** $0.006/мин аудио → часовой эпизод = $0.36. Пять лет назад сопоставимая точность стоила $30+.
- **FFmpeg-фильтры достаточно зрелые.** Subtitles filter + libass дают word-level karaoke-подсветку без ML-моделей.
- **Рынок готов платить.** Opus Clip за 2 года перешёл в $100M ARR [ASSUMPTION на основе публичных данных 2024]. Уже доказано, что креаторы платят.
- **Пробел в решениях.** Ни один из существующих инструментов не покрывает полный цикл "один upload → все артефакты". Каждый — вертикальный.

---

## 4. Целевая аудитория

**Основная: независимые подкастеры с аудиторией 1–20k слушателей/эпизод.**
- Кто: одиночки или пара host+guest, записывают 1–4 эпизода/мес
- Задача: дотянуться до новой аудитории, не тратя выходные на нарезку
- Текущий инструментарий: Opus Clip + Descript + ChatGPT вручную + Canva
- Willingness to pay: $30–60/мес за замену 2–3 текущих подписок

**Вторичная: маркетинговые агентства, которые управляют 3–10 подкастами клиентов.**
- Кто: in-house контент-маркетологи или SMM-агентства
- Задача: поточный репурпозинг с минимальным вмешательством менеджера
- Инструментарий: Opus Clip Business + ручная работа джуниоров
- Willingness to pay: $99–299/мес за multi-seat

**Не целевая аудитория (явный negative scope):**
- Крупные продакшены с выделенным пост-продакшн отделом — у них workflow уже отлажен
- Видео-подкастеры с уникальным визуальным стилем — наш generic 9:16 crop им не подойдёт
- Не-английские подкасты в v1 — Whisper хуже работает на малых языках, проверим позже

---

## 5. Архитектура

### Диаграмма слоёв

```
┌─────────────────────────────────────────────────────┐
│                      UI (React)                      │
│     Upload → Progress → Results/Regenerate           │
└───────────────────────┬─────────────────────────────┘
                        │ REST + SSE
┌───────────────────────▼─────────────────────────────┐
│                 Django API Layer                     │
│    POST /jobs  GET /jobs/:id  POST /regenerate      │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────┐
│                 Orchestrator                         │
│    Создаёт Job + Artifacts, fan-out в Celery        │
└───────────────────────┬─────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
┌───────▼──────┐ ┌──────▼──────┐ ┌─────▼────────┐
│  Transcriber │ │   Analyzer  │ │  Artifact    │
│   (Whisper)  │ │  (Claude)   │ │  Workers     │
└──────────────┘ └─────────────┘ │ (fan-out)    │
                                 └──────┬───────┘
                                        │
                     ┌──────────────────┼──────────────┐
                     │                  │              │
              ┌──────▼─────┐    ┌──────▼──────┐  ┌───▼─────┐
              │ VideoClip  │    │ TextArtifact│  │ Quote   │
              │ Worker     │    │ Worker      │  │ Worker  │
              │ (ffmpeg)   │    │ (Claude×N)  │  │(Playwr.)│
              └────────────┘    └─────────────┘  └─────────┘
                        │               │               │
                        └───────────────▼───────────────┘
                              PostgreSQL + Media Storage
```

### Стек

| Слой | Технология | Почему |
|---|---|---|
| Backend | Django 5 + DRF | Команда знает (CoMeet), быстро поднимается, batteries included |
| Async | Celery + Redis | Fan-out паттерн подходит идеально, мониторинг через Flower |
| БД | PostgreSQL 16 | Job state + artifacts metadata, `jsonb` для транскриптов |
| Хранилище | Local volume (MVP) → S3 (prod) | Переключается через Django `DEFAULT_FILE_STORAGE` |
| Транскрипция | OpenAI Whisper API | $0.006/мин, word-level timestamps out-of-box, не нужны GPU |
| LLM | Anthropic Claude (Sonnet 4.6) | 200k контекст помещает полный транскрипт, лучший tone-matching |
| Видео | FFmpeg (CLI через subprocess) | Стандарт индустрии, libass для word-subtitles |
| Графика | Pillow + Playwright | Pillow для простых шаблонов, Playwright (HTML→PNG) для сложной типографики |
| URL-парсинг | yt-dlp | Поддерживает YouTube, Spotify, SoundCloud |
| Фронтенд | React 18 + Vite + Tailwind | Быстрый dev-loop, команда знает |
| SSE/прогресс | Django SSE endpoint + EventSource | Проще WebSocket для однонаправленного прогресс-стрима |

### Модули с входами/выходами

| Модуль | Вход | Выход |
|---|---|---|
| `ingestion` | файл или URL | `raw_media_path`, `normalized_wav_path`, `duration_sec` |
| `transcription` | `normalized_wav_path` | `Transcript` (JSON: segments with word timestamps) |
| `analysis` | `Transcript` | `EpisodeAnalysis` (title, hook, clips[], themes[], quotes[], chapters[]) |
| `video_clip_worker` | `raw_media_path`, clip range, captions | `.mp4` файл 9:16, готовый к публикации |
| `text_artifact_worker` | `Transcript` + `EpisodeAnalysis` + artifact_type | Markdown/plain text |
| `quote_graphic_worker` | quote + brand_template | `.png` файл |
| `packager` | все артефакты | `.zip` для скачивания |

---

## 6. Монетизация

> **Для MVP хакатона — вне scope.** Ниже — план для после-хакатонного продолжения. В текущей сборке все функции бесплатные, без тарифов.

**Post-MVP план:**

| План | Цена | Эпизоды/мес | Длительность | Фичи |
|---|---|---|---|---|
| Free | $0 | 1 | до 60 мин | 3 клипа + show notes |
| Creator | $19/мес | 10 | до 120 мин | Все 8 артефактов, брендированные шаблоны |
| Pro | $49/мес | unlimited | до 180 мин | + AI-thumbnails, priority queue |
| Agency | $149/мес | unlimited | до 180 мин | Multi-seat (до 5), multi-brand |

**Юнит-экономика на эпизод (~60 мин):**
- Whisper API: ~$0.36
- Claude API (1 анализ + ~6 артефактов): ~$1.80–2.40 с учётом того, что транскрипт передаётся в каждый вызов
- Compute (FFmpeg на $20/мес VPS): negligible
- Playwright render: negligible
- **Итого переменной стоимости: ~$2.20–2.80/эпизод**
- Gross margin на Creator plan ($19 за 10 эпизодов): ~$19 − $28 = **отрицательный для heavy users**
- Фикс: либо кэшировать транскрипт между Claude-вызовами (Anthropic prompt caching), либо извлекать "meta"-контекст один раз и передавать дальше, либо поднять цену

---

## 7. Конкуренты

| Конкурент | Что делает | Чего не хватает | Наш ответ |
|---|---|---|---|
| Opus Clip | Вертикальные клипы с субтитрами и viral score | Только клипы. Нет текстовых артефактов, нет quote-графики | Мы выдаём 8 артефактов за те же деньги |
| Descript | Редактор + транскрипт + overdub | Сложный UI, требует ручного монтажа | Upload-and-done, без необходимости учиться |
| Castmagic | Show notes, социальные посты | Нет видео-клипов | У нас клипы первоклассные |
| Riverside | Запись + базовое редактирование | Не репурпозинг | Ортогональный продукт — мы можем быть downstream для них |
| ChatGPT + плагины | Текстовые артефакты | Не умеет генерировать видео-клипы с субтитрами | Интегрированный FFmpeg-пайплайн |
| Riverside Magic Clips | AI-clips внутри Riverside | Только для тех, кто записывает в Riverside | У нас platform-agnostic (любой upload) |

**Защитный ров (moat):**

1. **Интегрированная UX-петля regenerate.** Пользователь может пересоздать любой артефакт с новым seed за 10–30 секунд — это требует точной инфраструктуры воркеров и state tracking. Скопировать это как фичу легко, построить инфраструктуру — сложнее.
2. **Prompt + clip-ranking логика.** Это **не сам по себе moat** (скопируется за день) — но в связке с фидбек-петлёй от реальных пользователей (какие клипы они регенерируют, какие оставляют) становится дата-активом, который нельзя купить.
3. **Platform-agnostic позиция.** В отличие от Riverside Magic Clips, мы работаем с любым upload-ом — это расширяет TAM в 10+ раз.

---

## 8. План запуска

### Фаза 0 — Хакатон MVP (7 дней) **← мы здесь**

**Цель**: рабочая демонстрация "upload → 5 артефактов за 3 минуты" на коротком эпизоде (10–15 мин). Выиграть хакатон.

**Метрика успеха**: live demo 90 секунд от upload до видимых артефактов, без падений.

**Scope MVP (что ВКЛЮЧЕНО):**
- Upload файла (URL — опционально, если остаётся время)
- Транскрипция через Whisper API (без diarization — `ENABLE_DIARIZATION=0`)
- Semantic analysis через Claude
- 5 video clips (без face tracking — static center crop, `ENABLE_FACE_TRACKING=0`)
- LinkedIn post
- Twitter thread
- Show notes
- Progress UI с live-статусом
- Скачивание ZIP

**Scope MVP (что НЕ ВКЛЮЧЕНО — явный negative scope):**
- Speaker diarization — отложено, теряем 10% качества клипов, экономим день
- Face tracking reframe — отложено, static crop достаточно для демо
- AI-generated thumbnails — используем простой Pillow-шаблон
- Newsletter draft — если остаётся время в Day 6
- Quote graphics — если остаётся время в Day 6
- YouTube description — простой шаблон из `chapters[]`, без SEO-оптимизации
- Платежи, подписки, лимиты — вне scope
- Аутентификация сложнее email-only — вне scope
- Многоязычные подкасты — только английский

### Фаза 1 — Пост-хакатон (1 месяц)
Добавляем newsletter + quote graphics + thumbnails, базовую аутентификацию, биллинг через Stripe, Free tier для рекрутинга первых 100 пользователей.

### Фаза 2 — Scaling (3 месяца)
Speaker diarization через whisperX, face tracking, AI-thumbnails через Flux, brand templates (multiple per user), автопостинг в LinkedIn/X через их API (главный защитный ров).

---

## 9. Риски

| Риск | Вероятность | Митигация |
|---|---|---|
| Whisper API даёт плохой транскрипт на шумных подкастах | Средняя | Пре-обработка через ffmpeg `loudnorm` + `highpass` фильтры; fallback на локальный whisper-large-v3 |
| FFmpeg clip-рендер падает на edge-cases (нестандартные кодеки) | Высокая | Нормализация через ffmpeg на стадии ingestion в стандартный H.264/AAC; try/except в воркере с retry |
| Claude выбирает скучные моменты для клипов | Средняя | Итерация промпта на 5 реальных эпизодах в Day 2; human-in-the-loop кнопка "regenerate clip" |
| Celery очередь забивается на демо | Средняя | Load test в Day 7 на 3 параллельных upload-ах; отдельные очереди для быстрых (text) и медленных (video) задач |
| OpenAI/Anthropic rate limits блокируют демо | Низкая | Прогрев API до демо; кэшированный "demo mode" на готовом эпизоде как fallback |
| Wifi на демо-площадке медленный, upload не проходит | **Критическая** | Всегда иметь pre-uploaded эпизод, показать "new upload" как secondary, primary — "вот что мы сгенерировали заранее" с детальным breakdown |
| Субагенты Claude Code расходятся в код-стиле | Средняя | Строгий CLAUDE.md + rules с glob-паттернами; qa-reviewer проверяет каждый PR |
| Person A и Person B блокируют друг друга | Средняя | Жёсткий интерфейсный контракт между pipeline и UI (см. DIVISION_OF_WORK.md), мок-данные для Person B в Day 1–2 |

---

## 10. Техдетали

### Структура репозитория

```
podcast-pack/
├── CLAUDE.md                      # Главный конфиг для Claude Code (до 120 строк)
├── SPEC_TEMPLATE.md               # Шаблон для новой фичи
├── README.md                      # Для людей
├── docs/
│   ├── PROJECT_IDEA.md           # Этот документ (слой 1)
│   ├── SPEC.md                    # Техническая спецификация (слой 2)
│   ├── DIVISION_OF_WORK.md       # Разделение на Person A / B
│   └── SPEC_GENERATOR_PROMPT.md  # Генератор конфигурации (слой 3)
├── .claude/
│   ├── agents/                    # Субагенты (слой 4)
│   │   ├── pipeline-engineer.md
│   │   ├── video-engineer.md
│   │   ├── llm-prompt-engineer.md
│   │   ├── frontend-developer.md
│   │   └── qa-reviewer.md
│   ├── rules/                     # Контекстные правила
│   │   ├── celery-tasks.md
│   │   ├── ffmpeg-usage.md
│   │   ├── claude-api-usage.md
│   │   └── django-models.md
│   └── skills/                    # Навыки
│       ├── implement-artifact-worker.md
│       └── add-new-artifact-type.md
├── src/
│   ├── api/                       # DRF views
│   ├── pipeline/                  # ingestion, transcription, analysis
│   ├── workers/                   # Celery tasks
│   ├── models/                    # Django models
│   └── services/                  # внешние интеграции
├── frontend/
│   └── src/
│       ├── components/
│       └── pages/
├── scripts/                       # одноразовые скрипты для ручной проверки
├── tests/
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
└── manage.py
```

### Ключевые таблицы БД (preview, подробности в SPEC.md)

- **jobs** — `id`, `status` (enum), `source_type` (file|url), `source_path`, `duration_sec`, `created_at`, `completed_at`, `error`
- **transcripts** — `id`, `job_id (FK)`, `full_text`, `segments_json` (word timestamps), `language`
- **analyses** — `id`, `job_id (FK)`, `title`, `hook`, `themes_json`, `clip_candidates_json`, `chapters_json`, `quotes_json`
- **artifacts** — `id`, `job_id (FK)`, `type` (enum: clip/linkedin/twitter/shownotes/newsletter/quote/thumbnail/ytdesc), `status`, `file_path`, `text_content`, `metadata_json`, `version` (для regenerate)

### AI-пайплайн (единая точка истины)

| Шаг | Модель / Сервис | Структура вывода |
|---|---|---|
| Транскрипция | Whisper-1 API | `{segments: [{id, start, end, text, words: [{w, start, end}]}]}` |
| Анализ эпизода | Claude Sonnet 4.6 | Строгий JSON schema (см. SPEC.md §Analysis) |
| LinkedIn post | Claude Sonnet 4.6 | Plain markdown 300–500 слов |
| Twitter thread | Claude Sonnet 4.6 | JSON `{tweets: [string]}`, 6–10 элементов |
| Show notes | Claude Sonnet 4.6 | Markdown с секциями и таймкодами |
| Newsletter | Claude Sonnet 4.6 | Markdown 400 слов с CTA |

Все Claude-вызовы идут через единый `ClaudeClient` сервис (`src/services/claude_client.py`) с retry-логикой, prompt caching (для транскрипта как system message) и логированием usage.
