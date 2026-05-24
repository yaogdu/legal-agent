FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/opt/agent-runtime/src

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN python -c "import subprocess, tomllib; deps=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; subprocess.check_call(['pip','install','--no-cache-dir',*deps])"
COPY src ./src
COPY migrations ./migrations
COPY rag ./rag
COPY evaluations ./evaluations

RUN pip install --no-cache-dir --no-deps -e .

CMD ["legal-agent", "serve-api"]
