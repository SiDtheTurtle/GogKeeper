# lgogdownloader, pinned to an exact upstream commit.

FROM debian:trixie-slim AS build
ARG LGOG_TAG=v3.19
ARG LGOG_COMMIT=bb80e164dccfb72485932725b618ffa66854e7c4

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates cmake git ninja-build pkg-config \
      libboost-date-time-dev libboost-filesystem-dev libboost-iostreams-dev \
      libboost-program-options-dev libboost-regex-dev libboost-system-dev \
      libcurl4-openssl-dev libjsoncpp-dev librhash-dev libtidy-dev \
      libtinyxml2-dev zlib1g-dev

RUN git clone --branch "$LGOG_TAG" --depth 1 https://github.com/Sude-/lgogdownloader.git /src \
 && test "$(git -C /src rev-parse HEAD)" = "$LGOG_COMMIT" \
 && cmake -S /src -B /src/build -GNinja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
 && ninja -C /src/build lgogdownloader


FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libboost-date-time1.83.0 libboost-filesystem1.83.0 \
      libboost-iostreams1.83.0 libboost-program-options1.83.0 libboost-regex1.83.0 \
      libcurl4t64 libjsoncpp26 librhash1 libtidy58 libtinyxml2-11 tzdata zlib1g \
      python3 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/build/lgogdownloader /usr/local/bin/lgogdownloader

# Fails the build if a runtime library is missing from the list above.
RUN lgogdownloader --version

# uid 1000 matches the uid= option on the SMB library mount in compose.yml.
RUN useradd --uid 1000 --create-home gog \
 && mkdir -p /config /cache /library /data \
 && chown gog:gog /config /cache /library /data \
 && chmod 700 /config

# GogKeeper's own code. Owned by root, so it can't be changed at run time.
COPY gogkeeper/ /app/gogkeeper/

ENV USER=gog \
    XDG_CONFIG_HOME=/config \
    XDG_CACHE_HOME=/cache \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1

USER gog
WORKDIR /home/gog
# Refuses file commands that are missing the core parameters: see the script.
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["--help"]
