FROM python:3.14.6-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    opam \
    build-essential \
    git \
    m4 \
    pkg-config \
    libgmp-dev \
    && rm -rf /var/lib/apt/lists/*

RUN opam init --disable-sandboxing -y
RUN opam switch create 4.14.2 -y

RUN eval "$(opam env --switch=4.14.2)" && \
    opam install -y herdtools7.7.58

ENV PATH="/root/.opam/4.14.2/bin:${PATH}"

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

RUN python --version && \
    python -c "import networkx; print('NetworkX', networkx.__version__)" && \
    herd7 -version

WORKDIR /artifact

CMD ["bash"]