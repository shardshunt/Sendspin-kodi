FROM python:3.12-slim

# Install system dependencies (these worked perfectly in your log)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2t64 \
    libasound2-plugins \
    libportaudio2 \
    libopus0 \
    libflac14 \
    libpulse0 \
    pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

# Install sendspin via pip
RUN pip install --no-cache-dir sendspin

# Configure ALSA to use PulseAudio with larger buffers to prevent underruns
RUN echo 'pcm.pulse { type pulse }' > /root/.asoundrc && \
    echo 'ctl.pulse { type pulse }' >> /root/.asoundrc && \
    echo 'pcm.!default {' >> /root/.asoundrc && \
    echo '    type pulse' >> /root/.asoundrc && \
    echo '    buffer_size 8192' >> /root/.asoundrc && \
    echo '    period_size 2048' >> /root/.asoundrc && \
    echo '}' >> /root/.asoundrc && \
    echo 'ctl.!default { type pulse }' >> /root/.asoundrc

# Set the entrypoint to the daemon mode
ENTRYPOINT ["sendspin"]
CMD ["daemon"]
