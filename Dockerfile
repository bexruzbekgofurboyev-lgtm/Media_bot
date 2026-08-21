FROM python:3.12-slim

# FFmpeg + Deno uchun kerakli paketlar
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno o'rnatish
RUN curl -fsSL https://deno.land/install.sh | sh

# Deno PATH
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

WORKDIR /app

# Python dependencylar
COPY requirements.txt .

RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Bot kodlari
COPY . .

# Ishga tushirish
CMD ["python", "media_bot.py"]
