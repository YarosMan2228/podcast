---
name: django-models
globs:
  - "src/models/**/*.py"
  - "src/core/migrations/**/*.py"
---

# Django Models — правила

## 1. UUID как primary key — всегда

```python
import uuid
from django.db import models

class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ...
```

**Почему:**
- Не раскрываем внутренний порядок создания (конкуренты не узнают "у них только 150 job'ов")
- Безопасно экспонировать в URL (`/api/jobs/{uuid}`)
- Нет race condition на auto-increment при параллельных insert'ах

## 2. Обязательные timestamp поля

Каждая модель имеет:
```python
created_at = models.DateTimeField(auto_now_add=True, db_index=True)
updated_at = models.DateTimeField(auto_now=True)
```

- `auto_now_add=True` — устанавливается только при создании
- `auto_now=True` — обновляется при каждом save
- `db_index=True` на `created_at` для сортировки истории

## 3. Enum поля — через `choices`

```python
class JobStatus(models.TextChoices):
    PENDING = "PENDING"
    INGESTING = "INGESTING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    GENERATING = "GENERATING"
    PACKAGING = "PACKAGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class Job(models.Model):
    status = models.CharField(
        max_length=32,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        db_index=True,
    )
```

**Не используй**:
- `PositiveIntegerField` для статусов — нельзя grep'ать логи по `status=3`
- Просто `CharField` без `choices` — Django перестанет валидировать

## 4. Foreign keys с осознанным `on_delete`

```python
# Удаление job'а → удаление всех артефактов. Да, хотим каскад.
job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="artifacts")

# Удаление пользователя НЕ должно удалять его подкасты. Переназначаем (post-MVP).
user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
```

**Всегда думаем**: что происходит при удалении родителя? CASCADE — только когда child бесполезен без parent'а (наш случай для Transcript, Analysis, Artifact).

## 5. JSONB для структурированных данных

```python
segments_json = models.JSONField(default=dict)  # Postgres хранит как jsonb
```

**Правила:**
- `JSONField` используем для данных, которые **читаем целиком**: транскрипты, schemas, metadata
- Для данных, которые **фильтруем или ищем** — делаем отдельные колонки (например, `virality_score` как отдельное поле, а не в `metadata_json`)
- Не злоупотребляем — если поле всегда есть и имеет чёткий тип, оно заслуживает колонку

## 6. Индексы — явно

```python
class Meta:
    indexes = [
        models.Index(fields=["status", "created_at"]),  # для дашборда
        models.Index(fields=["-created_at"]),            # для истории
    ]
    constraints = [
        models.UniqueConstraint(fields=["job_id", "type", "index"], name="uniq_artifact"),
    ]
```

## 7. Миграции

**Каждое изменение модели = миграция в том же коммите.**

```bash
# ❌ плохой workflow
git pull
# забыли makemigrations
git push  # ломаем второго разработчика

# ✅
git pull
python manage.py makemigrations
python manage.py migrate  # проверяем что применилась
git add src/core/migrations/000X_....py src/models/
git commit -m "models: add Artifact.version field + migration"
```

**Обратимость**: Django обычно генерирует обратную миграцию автоматически. Для сложных data-миграций пиши `reverse_code`:
```python
operations = [
    migrations.RunPython(forward_func, reverse_code=reverse_func),
]
```

## 8. `__str__` для отладки

```python
class Job(models.Model):
    ...
    def __str__(self) -> str:
        return f"Job({self.id} · {self.status})"
```

Без этого Django admin / shell показывает `<Job: Job object (uuid)>` — бесполезно.

## 9. Managers для частых запросов

```python
class ArtifactManager(models.Manager):
    def ready_for_job(self, job_id):
        return self.filter(job_id=job_id, status="READY")
    
    def pending_for_job(self, job_id):
        return self.filter(job_id=job_id, status__in=["QUEUED", "PROCESSING"])

class Artifact(models.Model):
    objects = ArtifactManager()
    ...
```

Это чище, чем `Artifact.objects.filter(...).filter(...)` в каждом воркере.

## 10. Что НЕ делаем

- **Не используем `Model.objects.raw()`** с f-string — SQL injection
- **Не делаем `Model.objects.all()` без `.filter()`** в воркерах — легко положить БД на большой таблице
- **Не пишем бизнес-логику в `save()`** — это скрытый side-effect. Логика — в сервисных функциях.
- **Не забываем `related_name`** на ForeignKey — без него `job.artifact_set` вместо понятного `job.artifacts`
