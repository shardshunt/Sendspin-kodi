FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libportaudio2 \
    libasound2 \
    git \
    && rm -rf /var/lib/apt/lists/*


COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv/bin/uv
ENV PATH="/uv/bin:$PATH"
WORKDIR /app
RUN uv pip install --system "git+https://github.com/shardshunt/sendspin-cli-for-sendspin-kodi.git@main"
RUN mkdir -p /root/.config/sendspin

# Set the entrypoint to the daemon mode
ENTRYPOINT ["sendspin"]
CMD ["daemon"]
