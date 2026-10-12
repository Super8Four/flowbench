# Test-only derivative. Never publish this image or add these optional tools to
# the portable runtime image. Flent is GPLv3; its installed license stays intact.
FROM ubuntu:24.04 AS tools
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl python3-venv texinfo autoconf automake libtool patch \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY scripts/netperf-loopback-resolver.patch /build/netperf-loopback-resolver.patch
RUN curl --fail --location --retry 3 --max-time 120 \
    https://codeload.github.com/HewlettPackard/netperf/tar.gz/afc51ff9764741da4ed6702651fba9d9c23f8557 -o netperf.tar.gz \
    && echo "f7bca9d7faabd2bb27009013fa7df605f38a4e6a513697bad74355d2814b12cd  netperf.tar.gz" | sha256sum -c - \
    && tar -xzf netperf.tar.gz \
    && cd netperf-afc51ff9764741da4ed6702651fba9d9c23f8557 \
    && echo "1d08ee0d3154fa82af48d3f1ff44218c5a1317f0d95eed32721445656967451f  /build/netperf-loopback-resolver.patch" | sha256sum -c - \
    && patch --batch --fuzz=0 -p1 < /build/netperf-loopback-resolver.patch \
    && sha256sum /build/netperf-loopback-resolver.patch \
    && autoreconf -fi -I src/missing/m4 \
    && ./configure --enable-demo --enable-omni --prefix=/usr/local \
    && make -j2 \
    && install -Dm755 src/netperf /out/netperf \
    && install -Dm755 src/netserver /out/netserver \
    && install -Dm644 COPYING /out/LICENSE-netperf
RUN cp /build/netperf-loopback-resolver.patch /out/netperf-loopback-resolver.patch \
    && sha256sum /out/netperf-loopback-resolver.patch > /out/netperf-loopback-resolver.SHA256
RUN python3 -m venv /opt/flent-venv \
    && /opt/flent-venv/bin/pip install --no-cache-dir flent==2.3.0

FROM iperf-workspace:local
USER root
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping libcap2-bin \
    && if [ -n "$(getcap /usr/bin/ping)" ]; then setcap -r /usr/bin/ping; fi \
    && test -z "$(getcap /usr/bin/ping)" \
    && /usr/bin/ping -V \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tools /opt/flent-venv /opt/flent-venv
COPY --from=tools /out/netperf /usr/local/bin/netperf
COPY --from=tools /out/netserver /usr/local/bin/netserver
COPY --from=tools /out/LICENSE-netperf /usr/local/share/licenses/netperf/LICENSE
COPY --from=tools /out/netperf-loopback-resolver.patch /usr/local/share/licenses/netperf/netperf-loopback-resolver.patch
COPY --from=tools /out/netperf-loopback-resolver.SHA256 /usr/local/share/licenses/netperf/netperf-loopback-resolver.SHA256
ENV PATH="/opt/flent-venv/bin:$PATH"
USER 10001:10001
