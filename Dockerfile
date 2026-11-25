# TestForge web dashboard.
# Set TESTFORGE_PASSWORD when the container is reachable from the internet:
# TestForge executes the Python code submitted to it.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY testforge ./testforge
COPY examples ./examples
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install -e . \
 && python -m playwright install --with-deps chromium \
 && useradd --create-home --uid 10001 testforge \
 && chown -R testforge /app
USER testforge

# Hosting services such as Render set PORT; default 8000.
ENV PORT=8000 TESTFORGE_MAX_RUNS=2
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
CMD ["sh", "-c", "uvicorn testforge.web.app:app --host 0.0.0.0 --port ${PORT}"]
