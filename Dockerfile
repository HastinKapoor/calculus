FROM ubuntu:24.04

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

RUN herd7 -version

WORKDIR /artifact

CMD ["bash"]
