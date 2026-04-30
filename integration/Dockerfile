FROM python:3.12-slim
WORKDIR /app

ARG DEVPI_URL=https://pypi.munalabs.eu/muna/dev/+simple/

# Docker CLI — needed by DockerExecTrigger to run `docker exec ares-hermes hermes`
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends docker.io && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir \
    --index-url "$DEVPI_URL" \
    --extra-index-url https://pypi.org/simple/ \
    -r requirements.txt

COPY ares_integration/ ares_integration/

RUN adduser --disabled-password --no-create-home appuser
USER appuser

EXPOSE 8001
CMD ["python", "-m", "ares_integration"]
