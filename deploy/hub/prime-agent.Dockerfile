FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates ripgrep procps jq unzip xz-utils \
      build-essential cmake ninja-build clang gdb pkg-config libssl-dev \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pnpm \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 prime
USER prime
WORKDIR /home/prime
ENV RUSTUP_HOME=/home/prime/.rustup \
    CARGO_HOME=/home/prime/.cargo \
    PYTHONUSERBASE=/home/prime/.local
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal -c rustfmt -c clippy
RUN pip install --no-cache-dir --user ipykernel jupyter_client ipython pytest
RUN curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh | sh
RUN \
    /home/prime/.local/bin/uv pip install --python /home/prime/.prime/agent/kernel-venv/bin/python ipykernel jupyter_client ipython && \
    for s in /home/prime/.local/share/prime-agent/releases/*/skills/*/; do \
      /home/prime/.local/bin/uv pip install --python /home/prime/.prime/agent/kernel-venv/bin/python --editable "$s" || true; \
    done
ENV PATH="/home/prime/.local/bin:/home/prime/.cargo/bin:${PATH}" \
    npm_config_cache=/home/prime/.cache/npm
CMD ["sleep", "infinity"]
