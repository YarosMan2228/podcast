# DEPLOY.md — как выкатить на сервер

Один VPS (2 vCPU / 4 GB хватает на десятки эпизодов в день), Docker + Docker Compose v2, домен, указывающий на сервер. Всё остальное — в контейнерах.

## 1. Подготовить `.env`

```bash
cp .env.example .env
```

Обязательно заполнить:

| Переменная | Что |
|---|---|
| `OPENAI_API_KEY` | Whisper. Или Groq: `gsk_…` + `WHISPER_BASE_URL=https://api.groq.com/openai/v1` + `WHISPER_MODEL=whisper-large-v3-turbo` |
| `ANTHROPIC_API_KEY` | Claude |
| `DJANGO_SECRET_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DJANGO_ALLOWED_HOSTS` | `podcast.example.com` |
| `APP_ACCESS_TOKEN` | `python -c "import secrets;print(secrets.token_urlsafe(32))"` — без него сервис открыт всему интернету |
| `POSTGRES_PASSWORD` | любой случайный |

Опционально: `APP_MULTI_USER=1` (именные ключи, см. `docs/SECURITY.md`), `UPLOAD_RATE_LIMIT`, `TRANSCRIPTION_ALLOWED_LANGUAGES`.

`DJANGO_DEBUG=0` и `DJANGO_SECURE_PROXY_SSL_HEADER=1` overlay ставит сам.

## 2. Запустить

```bash
export SITE_ADDRESS=podcast.example.com     # или :80 для HTTP без домена
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Что произойдёт:

- `web` (Caddy) соберёт SPA, получит сертификат Let's Encrypt для `SITE_ADDRESS`, будет отдавать фронт и проксировать `/api` и `/media` в `app`.
- `app` применит миграции, напечатает результат preflight и поднимет gunicorn.
- `worker` возьмёт очереди `default,video,text_artifacts,graphics`.

Проверка:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -s https://podcast.example.com/api/health          # {"status":"ok"}
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app python manage.py preflight --probe
```

Открыть `https://podcast.example.com`, ввести `APP_ACCESS_TOKEN`, загрузить эпизод.

## 3. Multi-user (если нужно)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app \
  python manage.py access_key create --name "Alice"
```

Ключ печатается один раз. `list` / `revoke <name>` — там же.

## 4. Обновление

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Миграции применяются на старте `app`. Медиа лежит в volume `media`, база в `pgdata`, сертификаты в `caddy_data` — `down` без `-v` их не трогает.

## 5. Бэкап

```bash
docker compose exec db pg_dump -U postgres podcastpack | gzip > backup_$(date +%F).sql.gz
docker run --rm -v podcast-pack_media:/m -v "$PWD":/out alpine tar czf /out/media_$(date +%F).tgz -C /m .
```

## 6. Локальная проверка prod-сборки без домена

```bash
SITE_ADDRESS=:80 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
curl -s http://localhost/api/health
```

Затем `http://localhost` в браузере. Остановить: `docker compose -f docker-compose.yml -f docker-compose.prod.yml down`.

## Что не входит

- Резервное копирование по расписанию и мониторинг — по вкусу (cron + пункт 5, `docker compose logs -f`).
- Масштабирование воркеров: `docker compose … up -d --scale worker=3`, очереди в Redis общие.
- S3 вместо локального `media` volume: `MEDIA_ROOT` пока только диск.
