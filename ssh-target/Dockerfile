FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/run/sshd /etc/ssh/sshd_config.d /ca-keys \
 && useradd -m -s /bin/bash demo \
 && usermod -p '*' demo \
 && mkdir -p /home/demo/.ssh \
 && chown -R demo:demo /home/demo/.ssh \
 && chmod 700 /home/demo/.ssh

COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
 && ssh-keygen -A

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
