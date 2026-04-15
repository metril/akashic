FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY akashic/ akashic/
COPY alembic.ini .
COPY alembic/ alembic/

RUN apt-get update && apt-get install -y libldap2-dev libsasl2-dev && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir .

CMD ["uvicorn", "akashic.main:app", "--host", "0.0.0.0", "--port", "8000"]
