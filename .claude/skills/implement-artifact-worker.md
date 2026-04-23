# Skill: Implement a new artifact worker

Используй эту инструкцию, когда нужно добавить новый тип артефакта в пайплайн (например, добавляем `NEWSLETTER` или `EPISODE_THUMBNAIL` после того, как MVP core готов).

## Пример: добавляем `NEWSLETTER`

### Шаг 1 — Добавить тип в enum

`src/jobs/enums.py`:

```python
class ArtifactType(models.TextChoices):
    VIDEO_CLIP = "VIDEO_CLIP"
    LINKEDIN_POST = "LINKEDIN_POST"
    TWITTER_THREAD = "TWITTER_THREAD"
    SHOW_NOTES = "SHOW_NOTES"
    NEWSLETTER = "NEWSLETTER"   # ← добавили
    ...
```

Миграция не нужна — `TextChoices` хранится как varchar, новые значения работают сразу. НО: добавить проверку на frontend, что UI знает этот тип.

### Шаг 2 — Написать промпт

Новый файл `src/pipeline/prompts/newsletter.py`:

```python
from anthropic.types import MessageParam

def build_newsletter_prompt(
    transcript_text: str,
    analysis: dict,
    tone: str = "casual",
) -> tuple[list, list[MessageParam]]:
    system = [
        {"type": "text", "text": "You are a newsletter writer for indie podcasters..."},
        {
            "type": "text",
            "text": f"<transcript>{transcript_text}</transcript>",
            "cache_control": {"type": "ephemeral"},
        },
    ]
    user = f"""Episode: {analysis['episode_title']}
Hook: {analysis['hook']}
Key themes: {', '.join(analysis['themes'])}
Top quotes: {json.dumps(analysis['notable_quotes'][:3])}

Write a newsletter:
- Subject line (no CAPS, no emoji, max 60 chars, curiosity gap style)
- Hook paragraph (60–80 words, specific claim or story)
- 3 takeaways as h3 headings, each with 50–80 words of body
- CTA to listen to full episode
- 400 words total body
- Tone: {tone}

Return ONLY markdown. Subject on first line after `Subject: `, then blank line, then body."""
    return system, [{"role": "user", "content": user}]


def validate_newsletter(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("markdown").strip()
    if not text.startswith("Subject:"):
        raise PromptValidationError("Newsletter must start with 'Subject: ...'")
    word_count = len(text.split())
    if not (350 <= word_count <= 500):
        raise PromptValidationError(f"Word count {word_count} outside 350-500")
    banned_in_subject = ["🔥", "URGENT", "BREAKING"]
    subject_line = text.split("\n", 1)[0]
    for b in banned_in_subject:
        if b in subject_line:
            raise PromptValidationError(f"Subject contains banned: {b}")
    return text
```

### Шаг 3 — Создать воркер

Новый файл `src/workers/newsletter_worker.py`:

```python
from core.celery_app import app
from src.models import Artifact, Analysis, Transcript
from src.services.claude_client import claude_client
from src.services.events import publish
from src.pipeline.prompts.newsletter import build_newsletter_prompt, validate_newsletter


@app.task(bind=True, max_retries=3, soft_time_limit=300, acks_late=True)
def generate_newsletter(self, artifact_id: str, tone: str = "casual") -> None:
    art = Artifact.objects.select_related("job").get(id=artifact_id)
    art.status = "PROCESSING"
    art.save(update_fields=["status", "updated_at"])

    try:
        transcript = Transcript.objects.get(job_id=art.job_id)
        analysis = Analysis.objects.get(job_id=art.job_id)

        system, messages = build_newsletter_prompt(
            transcript_text=transcript.full_text,
            analysis={
                "episode_title": analysis.episode_title,
                "hook": analysis.hook,
                "themes": analysis.themes_json,
                "notable_quotes": analysis.quotes_json,
            },
            tone=tone,
        )
        response = claude_client.call(
            system=system, messages=messages, max_tokens=2000, temperature=0.7,
        )
        validated = validate_newsletter(response.text)

        art.text_content = validated
        art.status = "READY"
        art.metadata_json = {
            "tone": tone,
            "word_count": len(validated.split()),
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        art.save()
        publish(str(art.job_id), "artifact_ready", {"artifact_id": str(art.id), "type": "NEWSLETTER"})

    except Exception as e:
        art.status = "FAILED"
        art.error = str(e)[:2000]
        art.save()
        publish(str(art.job_id), "artifact_failed", {"artifact_id": str(art.id), "error": str(e)})
        raise
```

### Шаг 4 — Зарегистрировать в оркестраторе

В `src/workers/tasks.py::orchestrate_artifacts`:

```python
TEXT_TYPES_MAP = {
    "LINKEDIN_POST": generate_text_artifact,
    "TWITTER_THREAD": generate_text_artifact,
    "SHOW_NOTES": generate_text_artifact,
    "NEWSLETTER": generate_newsletter,      # ← добавили
    "YOUTUBE_DESCRIPTION": generate_text_artifact,
}

def orchestrate_artifacts(job_id: str) -> None:
    ...
    artifacts_plan = [
        ("VIDEO_CLIP", i, "video") for i in range(5)
    ] + [
        ("LINKEDIN_POST", 0, "text"),
        ("TWITTER_THREAD", 0, "text"),
        ("SHOW_NOTES", 0, "text"),
        ("YOUTUBE_DESCRIPTION", 0, "text"),
    ]
    if settings.ENABLE_NEWSLETTER:
        artifacts_plan.append(("NEWSLETTER", 0, "text"))  # ← добавили
    ...
```

Feature flag `ENABLE_NEWSLETTER` в `.env` — ставим `0` в MVP, `1` когда готово.

### Шаг 5 — Добавить компонент на frontend

`frontend/src/components/ArtifactCard.jsx`:

```jsx
const TYPE_DISPLAY = {
  VIDEO_CLIP: { icon: "🎬", label: "Clip" },
  LINKEDIN_POST: { icon: "💼", label: "LinkedIn" },
  NEWSLETTER: { icon: "📧", label: "Newsletter" },  // ← добавили
  ...
};

function ArtifactCard({ artifact, onRegenerate }) {
  const meta = TYPE_DISPLAY[artifact.type];
  // для newsletter - тот же рендер, что и для show_notes (markdown)
  if (artifact.type === "NEWSLETTER") {
    return <TextArtifact artifact={artifact} onRegenerate={onRegenerate} />;
  }
  ...
}
```

`TextArtifact.jsx` работает с markdown'ом через `react-markdown`, новый тип просто ре-использует компонент.

### Шаг 6 — Добавить в packager

`src/workers/packager.py`:

```python
TEXT_ARTIFACT_FILENAMES = {
    "LINKEDIN_POST": "linkedin.md",
    "TWITTER_THREAD": "twitter_thread.md",
    "SHOW_NOTES": "show_notes.md",
    "NEWSLETTER": "newsletter.md",          # ← добавили
    "YOUTUBE_DESCRIPTION": "youtube_description.txt",
}
```

И обновить `index.txt` шаблон, чтобы он упоминал newsletter в оглавлении.

### Чеклист после добавления

- [ ] Новый тип работает end-to-end на одном тестовом эпизоде
- [ ] Validator ловит характерные ошибки (вручную сломать output и проверить)
- [ ] Регенерация работает (`POST /api/artifacts/:id/regenerate` с `tone`)
- [ ] Frontend корректно отображает новый тип
- [ ] Packaging включает новый файл в ZIP
- [ ] Feature flag выключен по умолчанию до стабилизации
- [ ] Нет TODO-комментариев в коммите
