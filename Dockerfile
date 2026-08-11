# op25-docker
# Single-container P25 trunking scanner appliance:
#   - op25 (boatbod gr310) multi_rx.py
#   - icecast2 live audio streaming
#   - stream_runner (UDP audio -> ffmpeg -> icecast)
#   - FastAPI control plane (web UI + telemetry + config)

FROM debian:bookworm AS build

ENV DEBIAN_FRONTEND=noninteractive

# Build dependencies for op25 (gnuradio 3.10 OOT) - mirrors boatbod install.sh
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    pkg-config \
    gnuradio-dev \
    gr-osmosdr \
    libboost-all-dev \
    librtlsdr-dev \
    libuhd-dev \
    libitpp-dev \
    liblog4cpp5-dev \
    libsndfile1-dev \
    libasound2-dev \
    libsigc++-2.0-dev \
    libpcap-dev \
    libspdlog-dev \
    libusb-1.0-0-dev \
    liborc-dev \
    python3-dev \
    python3-numpy \
    python3-scipy \
    python3-pybind11 \
    python3-setuptools \
    python3-requests \
    && rm -rf /var/lib/apt/lists/*

# Build op25 (gr310 branch, for gnuradio 3.10). The gr310 tree is a top-level
# cmake project (see install.sh): build from the repo root, not from
# gr-op25_repeater/.
RUN git clone --branch gr310 --depth 1 https://github.com/boatbod/op25 /tmp/op25-src \
    && mkdir -p /tmp/op25-src/build \
    && cd /tmp/op25-src/build \
    && cmake -DCMAKE_BUILD_TYPE=Release ../ \
    && make -j$(nproc) \
    && make install \
    && ldconfig

# Install op25 python apps to a stable location (make install only installs *.py
# to bin; we need the whole apps tree for tdma/, gr_gnuplot.py, example data etc.)
RUN mkdir -p /opt/op25/apps \
    && cp -r /tmp/op25-src/op25/gr-op25_repeater/apps/. /opt/op25/apps/ \
    && rm -rf /opt/op25/apps/*.o \
    && echo "/usr/bin/python3" > /opt/op25/apps/op25_python

##############################################################################

FROM debian:bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/op25/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gnuradio \
    gr-osmosdr \
    librtlsdr0 \
    rtl-sdr \
    libuhd4.3.0 \
    libitpp8v5 \
    liblog4cpp5v5 \
    libsndfile1 \
    libasound2 \
    libpcap0.8 \
    libspdlog1.10 \
    libusb-1.0-0 \
    python3 \
    python3-numpy \
    python3-scipy \
    python3-requests \
    python3-venv \
    python3-pip \
    icecast2 \
    supervisor \
    ffmpeg \
    curl \
    procps \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy op25 build artifacts (C++ libs, python bindings, cmake config)
COPY --from=build /usr/local/ /usr/local/

# op25 python apps tree
COPY --from=build /opt/op25/apps /opt/op25/apps

# Project files
COPY conf/ /opt/op25/defaults/
COPY supervisor/ /opt/op25/supervisor/
COPY stream_runner.py render_configs.py /opt/op25/
COPY control-plane/ /opt/op25/control-plane/

RUN ldconfig

# Python web stack in a venv (avoids PEP-668 externally-managed env)
RUN python3 -m venv /opt/op25/venv \
    && /opt/op25/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/op25/venv/bin/pip install --no-cache-dir \
        fastapi==0.111.0 \
        "uvicorn[standard]==0.30.1" \
        httpx==0.27.0 \
        python-multipart==0.0.9

# Layout
RUN mkdir -p /etc/op25 /var/log/op25 /var/log/icecast2 /var/lib/icecast2 /var/run/op25 /opt/op25/conf

WORKDIR /opt/op25

EXPOSE 8080 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["/bin/sh", "/opt/op25/supervisor/entrypoint.sh"]
