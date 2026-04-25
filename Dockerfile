# Pinned to bookworm because Playwright's `--with-deps chromium` script still
# references Debian packages (ttf-ubuntu-font-family, ttf-unifont) that were
# removed in trixie. The unpinned `python:3.12-slim` tag now points at trixie
# so the build breaks. Re-evaluate when Playwright updates its deps list.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# FFmpeg для видео-пайплайна, git для некоторых pip-пакетов
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright браузеры для HTML→PNG рендеринга quote-графики
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
