FROM debian:bookworm-slim AS mihomo

ARG TARGETARCH
ARG MIHOMO_VERSION=v1.19.29
ARG MIHOMO_AMD64_SHA256=60de76a35a6cbf7b4fa4a20f5c257c24345d1d635ab1aa3877022a1997ef413c
ARG MIHOMO_ARM64_SHA256=9a868b5e4e0ad91d9d71e1b41b0cfce78aaba44360c30df74a723f8e3926a86c

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl gzip \
    && MIHOMO_ARCH="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "${MIHOMO_ARCH}" in \
         amd64) MIHOMO_SHA256="${MIHOMO_AMD64_SHA256}" ;; \
         arm64) MIHOMO_SHA256="${MIHOMO_ARM64_SHA256}" ;; \
         *) echo "Unsupported architecture: ${MIHOMO_ARCH}" >&2; exit 1 ;; \
       esac \
    && curl --fail --show-error --location --retry 3 \
         --output /tmp/mihomo.gz \
         "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-${MIHOMO_ARCH}-${MIHOMO_VERSION}.gz" \
    && echo "${MIHOMO_SHA256}  /tmp/mihomo.gz" | sha256sum --check --strict \
    && gzip --decompress /tmp/mihomo.gz \
    && chmod 0755 /tmp/mihomo

FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY --from=mihomo /tmp/mihomo /usr/local/bin/mihomo
COPY requirements.txt /app/requirements.txt
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --requirement /app/requirements.txt

COPY app /app/app
RUN mkdir --parents /app/config /data \
    && chmod 0700 /data

EXPOSE 8080

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
