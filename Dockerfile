# Railway ba'zan Nixpacks/Railpack orqali ffmpeg'ni to'g'ri o'rnatmasligi
# mumkin, shuning uchun Dockerfile orqali to'liq nazorat qilamiz.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "media_bot.py"]
