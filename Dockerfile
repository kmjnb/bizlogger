FROM m.daocloud.io/docker.io/library/python:3.11-alpine

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# asyncpg has a manylinux wheel; aiogram is pure-Python. No build deps required on alpine.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Media + temporary files persist via Coolify volume mounted at /app/storage.
RUN mkdir -p /app/storage/media

CMD ["python", "main.py"]
