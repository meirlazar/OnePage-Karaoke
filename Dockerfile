FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/root/.local/bin:$PATH" \
    TORCH_HOME=/root/.cache/torch \
    PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH="/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib:/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cuda_runtime/lib:${LD_LIBRARY_PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    fontconfig \
    fonts-dejavu \
    git \
    pkg-config \
    libavcodec-dev \
    libavdevice-dev \
    libavfilter-dev \
    libavformat-dev \
    libass-dev \
    libass9 \
    libavutil-dev \
    libsndfile1 \
    libswresample-dev \
    libswscale-dev \
    pipx \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cu118 \
        torch==2.1.2+cu118 torchvision==0.16.2+cu118 torchaudio==2.1.2+cu118 \
    && pip install --no-cache-dir -r /app/requirements.txt

RUN pipx install 'spotdl>=4.2,<5' \
    && ln -sf /root/.local/bin/spotdl /usr/local/bin/spotdl
