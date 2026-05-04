FROM python:3.12-slim

# Install system dependencies (these worked perfectly in your log)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2t64 \
    libasound2-plugins \
    libportaudio2 \
    libopus0 \
    libflac14 \
    && rm -rf /var/lib/apt/lists/*

# Install sendspin via pip
RUN pip install --no-cache-dir sendspin

# Set the entrypoint to the daemon mode
ENTRYPOINT ["sendspin"]
CMD ["daemon"]
