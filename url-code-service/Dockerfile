# Playwright's own image ships a matching Chromium + all its OS-level deps
# already installed -- doing that by hand on top of python:3.11-slim means
# tracking Chromium's apt dependency list yourself, which is exactly what
# this image exists to avoid. Keep the tag's Playwright version in step with
# requirements.txt's playwright== pin.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
EXPOSE 8080
# --timeout 120: the /api/resolve_js JS-rendering fallback needs headroom
# beyond gunicorn's 30s default (page load + JS settle time can take ~15-20s).
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "120", "app:app"]
