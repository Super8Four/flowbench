# syntax=docker/dockerfile:1
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential ca-certificates curl libssl-dev python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Use the upstream release archive and its published checksum. Ubuntu 24.04's
# older iPerf package lacks the full streaming JSON used by this interface.
WORKDIR /build
RUN curl --fail --location --retry 3 --max-time 120 \
        https://downloads.es.net/pub/iperf/iperf-3.22.tar.gz -o iperf.tar.gz \
    && echo "1c0d0fb02c52626111d6e132db80edfbf27bbaff8bd9245df2a371dcb0b35a92  iperf.tar.gz" | sha256sum -c - \
    && tar -xzf iperf.tar.gz \
    && cd iperf-3.22 \
    && ./configure --disable-shared --enable-static CFLAGS="-O2" LDFLAGS="-s" \
    && make -j2 \
    && install -Dm755 src/iperf3 /out/iperf3 \
    && install -Dm644 LICENSE /out/LICENSE-iperf3

COPY requirements.txt /build/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/venv/bin/pip uninstall -y pip setuptools

FROM ubuntu:24.04 AS runtime
ARG FLOWBENCH_VERSION=0.2.0-dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="FlowBench" \
      org.opencontainers.image.version=$FLOWBENCH_VERSION \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/Super8Four/flowbench"

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNS_DIRECTORY=/data/runs \
    IPERF_PATH=/usr/local/bin/iperf3

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates libssl3t64 iputils-tracepath iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 iperf \
    && useradd --uid 10001 --gid iperf --no-create-home --home-dir /data --shell /usr/sbin/nologin iperf \
    && mkdir -p /data/runs /opt/iperf-workspace/instance \
    && chown -R iperf:iperf /data /opt/iperf-workspace/instance

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /out/iperf3 /usr/local/bin/iperf3
COPY --from=builder /out/LICENSE-iperf3 /usr/local/share/licenses/iperf3/LICENSE
WORKDIR /opt/iperf-workspace
COPY app ./app
COPY wsgi.py THIRD_PARTY_NOTICES.md ./

USER 10001:10001
EXPOSE 5000/tcp 5201/tcp 5201/udp
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]
CMD ["python", "-m", "app.container"]
