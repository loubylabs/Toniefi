FROM python:3.12-slim

# ffmpeg does the probing, transcoding and splitting; everything else is Python.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# yt-dlp ships fixes within days of YouTube changing its player, and the layer
# above keys on requirements.txt, which never changes. Without something to
# break the cache, a rebuild replays a months-old yt-dlp and reintroduces
# exactly the pin that file's comment warns against.
ARG YTDLP_REFRESH=local
RUN pip install --no-cache-dir --upgrade yt-dlp

COPY app ./app

ENV LIBRARY_DIR=/library \
    WORK_DIR=/work \
    DATA_DIR=/data \
    UPLOAD_STAGE_DIR=/data/upload-staging \
    PYTHONUNBUFFERED=1

RUN mkdir -p /library /work /data/upload-staging
VOLUME ["/library", "/data"]

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
