# syntax=docker/dockerfile:1
FROM debian:bookworm-slim AS cura-deb

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && cd /tmp \
    && apt-get download cura \
    && dpkg-deb -x cura_*.deb /extracted \
    && rm -rf /var/lib/apt/lists/* /tmp/*.deb

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cura-engine \
        fdm-materials \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=cura-deb /extracted/usr/share/cura/resources /opt/cura/resources

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PATH=/opt/venv/bin:$PATH
ENV CURA_ENGINE_SEARCH_PATH=/opt/cura/resources:/usr/share/cura/resources
ENV CURA_MACHINE_DEF=/opt/cura/resources/definitions/prusa_i3.def.json

EXPOSE 8050

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8050"]
