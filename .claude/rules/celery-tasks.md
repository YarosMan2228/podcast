---
name: celery-tasks
globs:
  - "src/workers/**/*.py"
  - "src/pipeline/tasks.py"
---

# Celery Tasks — правила

Эти правила подгружаются автоматически при работе с любым Celery-связанным файлом.

## 1. Стандартный декоратор

Каждая task имеет **одинаковый декоратор**:

```python
@app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=300,      # 5 минут — SIGTERM, task может catch и залогировать
    time_limit=330,           # 5.5 минут — SIGKILL, hard kill
    acks_late=True,           # task ack'ается только после успеха
)
def my_task(self, job_id: str) -> None:
    ...
```

**Почему так:**
- `bind=True` — нужен `self` для `self.retry()` и доступа к `self.request.id`
- `acks_late=True` — если воркер крашнется в середине, RabbitMQ/Redis отдаст task другому воркеру
- `soft_time_limit < time_limit` — soft позволяет graceful cleanup в `SoftTimeLimitExceeded` handler

## 2. Очереди

| Очередь | Тип задач | Concurrency |
|---|---|---|
| `default` | orchestrator, packaging, misc | 4 |
| `video` | FFmpeg-воркеры (CPU-heavy) | 2 (не больше, CPU bottleneck) |
| `text` | Claude API вызовы для text artifacts (I/O-bound) | 6 |
| `graphics` | Playwright, Pillow | 2 |

Указываем при `apply_async`:
```python
generate_video_clip.apply_async(args=[artifact_id], queue='video')
```

## 3. Идемпотентность

Task может рестартовать при краше воркера или ручном retry. **Перед созданием записи** — всегда `get_or_create` или `update_or_create`:

```python
# ❌ плохо — дубликат при рестарте
Transcript.objects.create(job_id=job_id, segments_json=data)

# ✅ хорошо
Transcript.objects.update_or_create(
    job_id=job_id,
    defaults={'segments_json': data, 'full_text': text, ...}
)
```

## 4. Переход статусов только через helper

Все переходы `JobStatus` проходят через `src/workers/tasks.py::transition_job_status(job_id, from_status, to_status)`. Функция проверяет валидность перехода по enum из SPEC §1.1 и падает с `InvalidTransition` при нарушении.

**Не делаем напрямую** `Job.objects.filter(id=job_id).update(status='...')` — это обходит валидацию.

## 5. Публикация SSE events

После любого изменения, которое должно быть видно на фронте, вызываем:

```python
from src.services.events import publish

publish(job_id, event_type='status_changed', payload={'status': 'ANALYZING'})
publish(job_id, event_type='artifact_ready', payload={'artifact_id': str(art.id), 'type': art.type})
```

Внутри — `redis.publish(f"job:{job_id}", json.dumps({...}))`. Frontend EventSource подхватит.

## 6. Обработка ошибок

```python
try:
    result = external_api_call()
except (RequestException, TimeoutError) as e:
    # Транзиентная ошибка — retry с exponential backoff
    raise self.retry(exc=e, countdown=2 ** self.request.retries)
except ValidationError as e:
    # Постоянная ошибка — no retry, FAIL
    Artifact.objects.filter(id=artifact_id).update(status='FAILED', error=str(e))
    publish(job_id, 'artifact_failed', {'artifact_id': artifact_id, 'error': str(e)})
    return  # НЕ raise, иначе Celery попытается retry
```

## 7. Chaining

Следующую task'у запускаем **только после** успешного commit'а:

```python
def transcribe(self, job_id):
    ...  # вся работа
    # В самом конце, после того как transcript сохранён:
    analyze.apply_async(args=[job_id])
```

Не использовать Celery `chain()` для связей между крупными этапами — сложнее debug'ать. Простой `apply_async` прозрачнее.

## 8. Логирование

В начале каждой task:
```python
logger.info("task_started", extra={"task": "transcribe", "job_id": job_id})
```
В конце — `task_completed` или `task_failed`. Все логи включают `job_id` для trace'а через grep.

## 9. Что НЕ делаем

- **Не вызываем task из task'а синхронно** через `.get()` — это deadlock-prone. Всегда `apply_async`.
- **Не передаём большие объекты через args** (транскрипты на мегабайты). Передаём `job_id`, внутри task — читаем из БД.
- **Не делаем DB-запросы вне task body** (в module-level) — код выполняется при импорте, до готовности БД.
