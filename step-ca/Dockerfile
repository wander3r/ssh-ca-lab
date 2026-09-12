FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates bash \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 -d /home/step -s /bin/bash step
COPY step /usr/local/bin/step
COPY step-ca /usr/local/bin/step-ca
COPY entrypoint.sh /entrypoint.sh
COPY export-ready.sh /usr/local/bin/export-ready.sh
RUN chmod +x /usr/local/bin/step /usr/local/bin/step-ca /entrypoint.sh /usr/local/bin/export-ready.sh
USER step
ENV STEPPATH=/home/step
EXPOSE 9000
ENTRYPOINT ["/entrypoint.sh"]
