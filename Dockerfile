FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1 \
    fonts-noto-cjk bash shellcheck zip unzip poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY 과제제작가이드.txt ./과제제작가이드.txt
COPY 과제예시 ./과제예시
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app"]
