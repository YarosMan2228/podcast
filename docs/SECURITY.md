# SECURITY.md — pre-launch checklist (20 пунктов) и что за ними стоит в коде

Снимок: 2026-09-06. Проверка по чек-листу «20 must-do's before launch». Статус: ✅ сделано и покрыто тестами, ➖ не применимо к этой архитектуре, ⚠️ частично / зависит от деплоя.

| # | Пункт | Статус | Где / как |
|---|---|---|---|
| 1 | Hide API keys | ✅ | `.env` в `.gitignore` и `.dockerignore`; ключи только через `settings.*`; preflight больше не печатает даже префикс ключа в 503/логи (`services/preflight.py`). Тест: `test_preflight_503_does_not_echo_key_material`. |
| 2 | Enable RLS | ➖ | Один арендатор, нет пользовательских таблиц — Postgres RLS нечего разделять. При появлении аккаунтов: `job.owner_id` + RLS по `current_setting('app.user_id')`. |
| 3 | Test IDOR | ✅ | Все id — UUIDv4 (неперебираемые). При `APP_ACCESS_TOKEN` любой `/api/jobs/:id` без токена → 401 (`test_gate_protects_job_records`). |
| 4 | Scan GIT secrets | ✅ | `git log -p --all` по паттернам `sk-…`, `sk-ant-…`, `gsk_…`, `AKIA…`, `ghp_…` — только тестовая заглушка `sk-ant-test-fixture-key-not-real`. `.env` никогда не был закоммичен. |
| 5 | Lock admin routes | ➖ | `django.contrib.admin` не подключён, роутов нет. |
| 6 | User isolation | ⚠️ | Аккаунтов нет; изоляция = один общий токен на инсталляцию (`api/middleware.py`). Multi-user — следующий этап. |
| 7 | Rate limit APIs | ✅ | `api/throttles.py`: 300/min на любой `/api/` + 20/hour на создание эпизодов (`upload`, `from_url`), per-IP, через Redis-кэш. Regenerate: 3/мин на артефакт (SPEC §6.5). `Retry-After` в 429. |
| 8 | Lock storage buckets | ✅ | Локальный `MEDIA_ROOT`; `/media/` за тем же токеном, отдаётся с `nosniff` + `CSP: sandbox`, path traversal отбивается (`test_media_path_traversal_is_blocked`). |
| 9 | Validate all inputs | ✅ | UUID, MIME по содержимому + расширению, размер ≤500 MB, `brand_color` regex, `podcast_name` ≤120, `hint` ≤300, `clip_layout`/`caption_style` enum, `tone` enum, `limit` clamp, `part` enum. |
| 10 | Block unauthenticated routes | ✅ | `AccessTokenMiddleware`: заголовок `X-Access-Token`, cookie `pp_access` (HttpOnly, SameSite=Lax, Secure за TLS) или одноразовый `?token=`. Открыты только `/api/health` и `/api/auth/session`. |
| 11 | SQL injection | ✅ | Только Django ORM, `grep` по `.raw(`/`cursor()`/`RawSQL`/`.extra(` пуст. |
| 12 | Remove sensitive logs | ✅ | Ключи не логируются; SSE bad-payload лог обрезан до 200 символов; ошибки вендоров (OpenAI/Anthropic) сами маскируют ключ. |
| 13 | Block field tampering | ✅ | Каждый endpoint читает только свои поля; `version`/`status`/`metadata_json`/`job_id` из тела игнорируются (`test_regenerate_ignores_tampered_fields`). |
| 14 | Restrict file uploads | ✅ | Аудио/видео: MIME+расширение, ffmpeg решает по содержимому. Логотип: **только PNG/JPEG/WebP, проверка байтов Pillow**, ≤2 MB, ≤4096² px; SVG запрещён (stored-XSS через `/media`). Имя файла санитизируется от `../`. |
| 15 | Secure server logic | ✅ | Вся валидация и переходы статусов на сервере (`transition_job_status`), клиент не может выставить статус. |
| 16 | Trim API responses | ✅ | В ответах нет путей ФС и ключей (`test_job_payload_has_no_filesystem_paths`); только URL под `/media/`. Ошибки пайплайна содержат текст исключения — допустимо для single-tenant, при multi-user обрезать. |
| 17 | Secure auth sessions | ✅ | Cookie HttpOnly + SameSite=Lax + Secure (за TLS с `DJANGO_SECURE_PROXY_SSL_HEADER=1`), сравнение `hmac.compare_digest`, `DELETE /api/auth/session` = logout. `SECRET_KEY` по умолчанию блокирует старт при `DEBUG=0`. |
| 18 | Scan dependencies | ✅ | `npm audit` → 0. `pip-audit`: подняты `Pillow 12.3.0`, `yt-dlp 2026.7.4`, `requests 2.33.0`, `python-dotenv 1.2.2`, `pytest 9.0.3`, `pytest-django 4.11.1`, `black 26.3.1`. Повторять перед релизом: `PYTHONUTF8=1 python -m pip_audit -r requirements.txt`. |
| 19 | Test record access | ✅ | `tests/test_security_hardening.py::TestAccessTokenGate` — API, media, delete без токена → 401; с заголовком/cookie/`?token=` → 200. |
| 20 | Attack your own app | ✅ | Тесты-атаки: traversal `/media/../`, SVG со `<script>`, HTML под видом PNG, подмена полей в regenerate, 4-й upload при лимите 3, ключ-плейсхолдер в 503. |

Дополнительно включено: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, CORS открыт только в DEBUG (в проде `CORS_ALLOWED_ORIGINS`).

## Как включить защиту на публичном URL

```
APP_ACCESS_TOKEN=<длинная случайная строка>   # python -c "import secrets;print(secrets.token_urlsafe(32))"
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<другая случайная строка>
DJANGO_ALLOWED_HOSTS=your.domain
DJANGO_SECURE_PROXY_SSL_HEADER=1               # если TLS терминирует nginx/Caddy
```

Фронт при первом открытии спросит токен и положит его в HttpOnly-cookie; ссылки на видео/PNG/ZIP работают через ту же cookie.

## Что осознанно не сделано

- Пользовательские аккаунты и per-user изоляция (пункты 2, 6) — общий токен на инсталляцию.
- WAF / защита от DDoS на уровне сети — задача reverse-proxy, не приложения.
- Антивирусный скан загружаемого аудио/видео — ffmpeg работает в контейнере без сети, файл никогда не исполняется.
