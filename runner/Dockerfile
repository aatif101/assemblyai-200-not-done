# One image for every task, condition and agent. Nothing task-specific, no keys.
FROM python:3.12-slim

ARG CLAUDE_CODE_VERSION=2.1.296
ARG CODEX_VERSION=0.169.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates git procps \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} @openai/codex@${CODEX_VERSION} \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Claude Code refuses to skip permission prompts as root, and the agent should not be root anyway.
RUN useradd -m -s /bin/bash agent && mkdir -p /work /out /fixtures && chown agent:agent /work /out
USER agent
ENV PATH="/home/agent/.local/bin:${PATH}" PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
WORKDIR /work
CMD ["sleep", "infinity"]
