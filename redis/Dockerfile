FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends redis-server \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /data
VOLUME /data
EXPOSE 6379
CMD ["redis-server", "--daemonize", "no", "--bind", "0.0.0.0", "--protected-mode", "no", "--dir", "/data", "--appendonly", "yes", "--maxmemory", "32mb", "--maxmemory-policy", "allkeys-lru", "--save", "60", "1"]
