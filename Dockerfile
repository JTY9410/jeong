FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . /app

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["gunicorn", "-b", "0.0.0.0:8000", "run:app", "--workers", "2", "--threads", "4", "--timeout", "120"]

