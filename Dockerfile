FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates ripgrep ffmpeg gcc python3-dev libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Docker CLI only (no daemon)
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install Node.js 22
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install hermes-agent v0.6.0
RUN git clone --depth 1 --branch v2026.3.30 https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent && \
    cd /opt/hermes-agent && \
    uv venv --python 3.11 venv && \
    . venv/bin/activate && \
    uv pip install -e ".[all]" && \
    npm install

ENV HERMES_HOME=/root/.hermes
ENV PATH="/opt/hermes-agent/venv/bin:$PATH"
ENV PYTHONPATH="/opt/hermes-agent"
WORKDIR /opt/hermes-agent

RUN mkdir -p $HERMES_HOME && \
    cp cli-config.yaml.example $HERMES_HOME/config.yaml && \
    cp .env.example $HERMES_HOME/.env

COPY skills/ /opt/hermes-agent/skills/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
