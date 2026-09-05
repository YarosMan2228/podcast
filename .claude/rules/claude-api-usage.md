---
name: claude-api-usage
globs:
  - "src/services/claude_client.py"
  - "src/pipeline/prompts/**/*.py"
  - "src/pipeline/analysis.py"
  - "src/workers/text_artifact_worker.py"
---

# Claude API Usage — правила

## 1. Один клиент для всех вызовов

Вся работа с Anthropic API идёт через `src/services/claude_client.py::ClaudeClient`. Не импортируем `anthropic` напрямую в воркерах/промптах.

```python
# ❌ плохо
from anthropic import Anthropic
client = Anthropic()
response = client.messages.create(...)

# ✅ хорошо
from src.services.claude_client import claude_client
response = claude_client.call(system=system, messages=messages, max_tokens=1500)
```

В `ClaudeClient.call` уже зашиты:
- Retry на `RateLimitError`, `APIError` с exponential backoff (1, 2, 4, 8 секунд, макс 4 попытки)
- Логирование `input_tokens`, `output_tokens`, `job_id`, `duration_ms`
- Извлечение текста из `response.content[0].text`

## 2. max_tokens всегда явно

```python
# ❌ max_tokens default = 1024, часто мало
response = client.messages.create(model=..., messages=...)

# ✅
response = claude_client.call(..., max_tokens=8000)  # осознанно
```

**Наши значения:**
- Analysis (big JSON): `max_tokens=8000`
- LinkedIn post: `max_tokens=1500`
- Twitter thread (JSON): `max_tokens=2000`
- Show notes: `max_tokens=3000`
- Newsletter: `max_tokens=2000`
- YouTube description: `max_tokens=1500`

## 3. Модель и temperature

- **Модель для всех MVP-вызовов**: `claude-sonnet-4-6` (берём из `settings.CLAUDE_MODEL`, не хардкод)
- **Temperature**:
  - `0.3` для analysis (нужна предсказуемая структура)
  - `0.7` для первой генерации text artifacts
  - `0.9` для регенерации (нужна вариативность)

## 4. Prompt caching для транскрипта

Транскрипт — самая большая часть input'а (10–20k токенов). Кэшируем его:

```python
system = [
    {
        "type": "text",
        "text": "You are a podcast content expert...",
    },
    {
        "type": "text",
        "text": f"<transcript>{full_text}</transcript>",
        "cache_control": {"type": "ephemeral"},  # 5-минутный кэш
    },
]
```

Первый вызов (analysis) прогревает кэш. Все последующие text-artifact вызовы в течение 5 минут **читают кэшированный транскрипт за 10% стоимости**. Экономия на эпизоде ~$1.

## 5. Structured outputs через JSON schema в промпте

Claude не имеет формального "JSON mode" как OpenAI. Заставляем возвращать JSON через:

1. **Явную схему в user message:**
```python
user = f"""Return JSON matching this exact structure:
{{
  "episode_title": "<string, max 60 chars>",
  "hook": "<string, max 120 chars>",
  "clip_candidates": [
    {{"start_ms": int, "end_ms": int, "virality_score": int, "reason": str}}
  ]
}}

Return ONLY JSON. No preamble, no markdown fences."""
```

2. **Валидация через pydantic:**
```python
from pydantic import BaseModel, ValidationError

class EpisodeAnalysisSchema(BaseModel):
    episode_title: str
    hook: str
    clip_candidates: list[ClipCandidate]
    ...

try:
    parsed = EpisodeAnalysisSchema.model_validate_json(response_text)
except ValidationError as e:
    # Retry с указанием ошибки
    retry_msg = f"Previous response failed validation: {e.errors()[0]}. Fix and return ONLY valid JSON."
```

3. **Strip markdown fences** на всякий случай:
```python
text = response_text.strip()
if text.startswith("```"):
    text = text.split("```", 2)[1]
    if text.startswith("json"):
        text = text[4:]
    text = text.rstrip("`").strip()
```

## 6. Retry стратегия для structured outputs

- **1-я попытка**: основной промпт
- **2-я попытка** (если JSON invalid): тот же промпт + `{"role": "assistant", "content": response_text}` + `{"role": "user", "content": f"That JSON is invalid: {error}. Return ONLY valid JSON matching the schema."}`
- **3-я попытка**: temperature=0, assistant prefix `{` для force-start с JSON
- **4-я попытка**: FAIL

## 7. Логирование usage

Каждый вызов логируется:
```python
logger.info("claude_call", extra={
    "job_id": job_id,
    "prompt_name": "analysis",
    "input_tokens": response.usage.input_tokens,
    "cache_read_tokens": response.usage.cache_read_input_tokens,
    "output_tokens": response.usage.output_tokens,
    "duration_ms": duration_ms,
})
```

Это позволит посчитать реальную стоимость эпизода после демо.

## 8. Что НЕ делаем

- **Не полагаемся на "Claude обычно возвращает правильно"** — всегда валидация + retry
- **Не используем streaming** в MVP — усложняет retry-логику, SSE в frontend всё равно использует уже готовые результаты
- **Не передаём `system` как строку** — используем list формат для cache_control блоков
- **Не храним API ключи в коде** — только `os.environ["ANTHROPIC_API_KEY"]` или `settings.ANTHROPIC_API_KEY`
