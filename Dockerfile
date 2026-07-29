FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    M3U8_DATA_ROOT=/data \
    M3U8_DOWNLOAD_ROOT=/downloads \
    YTDLP_NO_PLUGINS=1 \
    # 新增：设置国内 PyPI 镜像源，pip 安装也会变快
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# 关键修复：将 deb.debian.org 替换为阿里云镜像
RUN sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://security.debian.org|http://mirrors.aliyun.com/debian-security|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg gosu tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN python -m pip install --no-cache-dir . \
    && mkdir -p /data/work /data/logs /downloads \
    && chown -R app:app /data /downloads \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]