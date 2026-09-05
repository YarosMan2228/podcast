# Skill: Add a new REST endpoint

Используй эту инструкцию для добавления любого нового HTTP endpoint — `GET /api/jobs/:id/retry`, `POST /api/feedback`, etc.

## Пример: добавляем `POST /api/jobs/:id/cancel`

### Шаг 1 — View в `src/api/views/`

Новый файл или расширение существующего `src/api/views/jobs.py`:

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from src.models import Job
from src.services.events import publish


class CancelJobView(APIView):
    """POST /api/jobs/<uuid:job_id>/cancel — cancel a running job."""

    def post(self, request, job_id: str):
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response(
                {"error": {"code": "JOB_NOT_FOUND", "message": "Job does not exist"}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if job.status in ("COMPLETED", "FAILED"):
            return Response(
                {"error": {
                    "code": "JOB_ALREADY_FINISHED",
                    "message": f"Job is already in {job.status} state and cannot be cancelled",
                }},
                status=status.HTTP_409_CONFLICT,
            )

        # Помечаем FAILED с причиной cancelled-by-user
        Job.objects.filter(id=job_id).update(status="FAILED", error="CANCELLED_BY_USER")
        publish(str(job_id), "status_changed", {"status": "FAILED", "reason": "CANCELLED_BY_USER"})

        return Response(
            {"job_id": str(job_id), "status": "FAILED"},
            status=status.HTTP_200_OK,
        )
```

**Правила:**
- Класс-базированные views (DRF `APIView`) для консистентности с остальными endpoint'ами
- Строгий формат ошибок: `{"error": {"code": "...", "message": "..."}}` (см. SPEC §1.6)
- `try/except` вокруг `Model.objects.get()` — не использовать `get_object_or_404` (даёт 404 в не-JSON формате)

### Шаг 2 — URL в `src/core/urls.py`

```python
from src.api.views.jobs import JobDetailView, JobEventsView, JobDownloadView, CancelJobView

urlpatterns = [
    ...
    path("api/jobs/<uuid:job_id>/cancel", CancelJobView.as_view(), name="cancel-job"),
]
```

**Правила:**
- `<uuid:job_id>` конвертер, не `<str>` — Django валидирует формат UUID автоматически
- `name=` для reverse-lookups (пригодится в тестах)
- Добавляем ПОД существующей группой `/api/jobs/...`, чтобы url-файл оставался читаемым

### Шаг 3 — Serializer (если нужен request body)

Для endpoint'а с body используем DRF `serializers.Serializer`:

```python
# src/api/serializers.py
from rest_framework import serializers


class CancelJobRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)
```

Во view:
```python
def post(self, request, job_id):
    serializer = CancelJobRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": {
                "code": "INVALID_BODY",
                "message": "Request body validation failed",
                "field": list(serializer.errors.keys())[0],
            }},
            status=status.HTTP_400_BAD_REQUEST,
        )
    reason = serializer.validated_data.get("reason", "")
    ...
```

Для endpoint'а без body (как наш cancel) — serializer не нужен.

### Шаг 4 — Error codes — единый формат

**Формат ответа (SPEC §1.6):**
```json
{"error": {"code": "UPPER_SNAKE_CASE", "message": "Human readable message", "field": "optional_field_name"}}
```

**Правила кодов:**
- UPPER_SNAKE_CASE всегда
- Префикс сущности: `JOB_NOT_FOUND`, `ARTIFACT_NOT_FOUND`, `UPLOAD_TOO_LARGE`
- Уникальны на всё приложение — одно и то же условие → один и тот же код
- Реестр кодов — в SPEC.md в секции соответствующего модуля

**Типичные HTTP статусы для наших кейсов:**

| Код | Когда |
|---|---|
| `200` | Успех (GET, иногда POST без ресурса) |
| `201` | Успех, создан ресурс (POST /jobs/upload) |
| `202` | Принято, обработка асинхронная (POST /regenerate) |
| `400` | Ошибка в теле/параметрах запроса |
| `404` | Ресурс не найден |
| `409` | Конфликт состояния (job уже завершён, артефакт не регенерится больше) |
| `413` | Request too large (upload > 500MB) |
| `422` | Semantically invalid (URL валидный, но yt-dlp не смог) |
| `429` | Rate limited |
| `500` | Internal error (логируем stacktrace, юзеру не показываем) |

### Шаг 5 — Тест

Новый файл `tests/test_cancel_job.py`:

```python
import pytest
from rest_framework.test import APIClient
from src.models import Job


@pytest.mark.django_db
def test_cancel_running_job():
    job = Job.objects.create(status="ANALYZING", source_type="file")
    client = APIClient()
    response = client.post(f"/api/jobs/{job.id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "FAILED"
    job.refresh_from_db()
    assert job.status == "FAILED"
    assert job.error == "CANCELLED_BY_USER"


@pytest.mark.django_db
def test_cancel_completed_job_returns_409():
    job = Job.objects.create(status="COMPLETED", source_type="file")
    client = APIClient()
    response = client.post(f"/api/jobs/{job.id}/cancel")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_ALREADY_FINISHED"


@pytest.mark.django_db
def test_cancel_nonexistent_job_returns_404():
    client = APIClient()
    response = client.post("/api/jobs/550e8400-e29b-41d4-a716-446655440000/cancel")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"
```

**Минимум тестов для любого endpoint'а:**
- Happy path
- Ресурс не найден → 404
- Неправильное состояние → 409 (если applicable)
- Невалидный body → 400 (если applicable)

Запуск: `pytest tests/test_cancel_job.py -v`

### Чеклист перед коммитом

- [ ] View класс-базированный, APIView или generic view
- [ ] Все коды ошибок в формате SPEC §1.6
- [ ] URL в `src/core/urls.py` с UUID-конвертером
- [ ] Минимум 3 теста: happy path + not found + conflict/invalid
- [ ] Документация endpoint'а в SPEC.md соответствующего модуля обновлена (или создан новый подраздел)
- [ ] Нет DB-запросов вне view (в serializer нельзя — они pure validation)
- [ ] `publish()` SSE event там, где изменение нужно увидеть в UI в реальном времени
