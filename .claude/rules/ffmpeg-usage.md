---
name: ffmpeg-usage
globs:
  - "src/pipeline/ffmpeg_clip.py"
  - "src/pipeline/ass_subtitles.py"
  - "src/pipeline/ingestion.py"
  - "src/workers/video_clip_worker.py"
---

# FFmpeg Usage — правила

## 1. Вызов только через subprocess.run, без shell

```python
# ❌ ОПАСНО — shell injection возможен если input_path содержит спецсимволы
subprocess.run(f"ffmpeg -i {input_path} output.mp4", shell=True)

# ✅ ПРАВИЛЬНО
subprocess.run(
    ["ffmpeg", "-y", "-i", input_path, "output.mp4"],
    capture_output=True,
    text=True,
    timeout=120,
    check=False,  # сами проверяем returncode, чтобы логировать stderr
)
```

**Правила:**
- `shell=False` всегда (default, но явно не передавать `shell=True`)
- Команда — `list[str]`, никогда конкатенация строк
- `timeout=120` на обычные операции, `timeout=300` на длинные клипы
- `check=False` + ручная проверка `returncode`, чтобы логировать stderr

## 2. Обработка returncode

```python
result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
if result.returncode != 0:
    logger.error(
        "ffmpeg_failed",
        extra={"cmd": " ".join(cmd), "stderr": result.stderr[-2000:]},  # последние 2кб stderr
    )
    raise FFmpegError(f"ffmpeg exited {result.returncode}: {result.stderr[-500:]}")
```

Обрезка stderr до 2000 символов — чтобы не захламлять логи полным выводом ffmpeg.

## 3. Стандартные команды для этого проекта

### Нормализация для Whisper (mono 16kHz PCM WAV)
```python
["ffmpeg", "-y",
 "-i", input_path,
 "-ac", "1",           # mono
 "-ar", "16000",       # 16kHz
 "-c:a", "pcm_s16le",  # PCM 16-bit little-endian
 output_wav_path]
```

### Определение длительности (ffprobe)
```python
result = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "csv=p=0", input_path],
    capture_output=True, text=True, timeout=30, check=False,
)
duration_sec = float(result.stdout.strip())
```

### Вертикальный клип 9:16 с субтитрами
```python
["ffmpeg", "-y",
 "-ss", str(start_sec - 1),           # fast seek до сегмента (с 1-сек padding)
 "-i", input_path,
 "-ss", "1",                           # slow seek для точности после декодирования
 "-t", str(duration_sec + 2),         # длина с padding
 "-vf",
 f"scale=w=1080:h=1920:force_original_aspect_ratio=decrease,"
 f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
 f"subtitles='{ass_path}':force_style='Fontname=Inter,Fontsize=72'",
 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
 "-pix_fmt", "yuv420p",
 "-c:a", "aac", "-b:a", "128k",
 "-movflags", "+faststart",
 output_path]
```

### Waveform-видео для audio-only
```python
["ffmpeg", "-y",
 "-ss", str(start_sec - 1),
 "-i", input_audio_path,
 "-t", str(duration_sec + 2),
 "-filter_complex",
 "[0:a]showwaves=s=1080x1080:mode=cline:colors=white,"
 "format=yuv420p,pad=1080:1920:0:420:black[bg];"
 f"[bg]subtitles='{ass_path}'[v]",
 "-map", "[v]", "-map", "0:a",
 "-c:v", "libx264", "-preset", "veryfast",
 "-c:a", "aac",
 "-movflags", "+faststart",
 output_path]
```

## 4. ASS path escaping

В ffmpeg `subtitles=` filter путь должен быть escaped:
- Обратные слеши → `\\\\`
- Двоеточия → `\\:`
- Одинарные кавычки → `\\'`

В Linux-контейнере (наш случай) проще: всегда размещать `.ass` в `/tmp/`, путь вида `/tmp/sub_<uuid>.ass` не требует экранирования.

## 5. Временные файлы

```python
import tempfile, os
ass_path = os.path.join(tempfile.gettempdir(), f"sub_{uuid.uuid4().hex}.ass")
try:
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    run_ffmpeg(...)
finally:
    if os.path.exists(ass_path):
        os.remove(ass_path)
```

## 6. Проверка output'а

После ffmpeg — всегда проверить, что output существует и не пустой:

```python
if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
    raise FFmpegError(f"Output missing or too small: {output_path}")
```

## 7. Что НЕ делаем

- **Не используем `-re`** (realtime input pacing) — это для стримингa, не для batch-процессинга, замедляет в 60 раз
- **Не используем `-c copy`** когда нужна перекодировка — клипы должны быть в H.264 baseline для мобильных
- **Не пишем output в тот же путь что input** — ffmpeg может сегфолтнуть на overwrite
- **Не пропускаем `-y`** — при перезапуске task'а ffmpeg повиснет на prompt "Overwrite? [y/N]"
