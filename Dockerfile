FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"
COPY src/ ./src/
COPY skills/ ./skills/
COPY scripts/ ./scripts/
RUN mkdir -p /app/sandbox
EXPOSE 8003
CMD ["nwo-skill", "serve"]
