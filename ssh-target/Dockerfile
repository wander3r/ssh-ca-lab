FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server ca-certificates sudo \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/run/sshd /etc/ssh/sshd_config.d /ca-keys /etc/ssh/auth_principals \
 && for u in sre dev demo; do \
      useradd -m -s /bin/bash "$u" \
      && usermod -p '*' "$u" \
      && mkdir -p "/home/$u/.ssh" \
      && chown -R "$u:$u" "/home/$u/.ssh" \
      && chmod 700 "/home/$u/.ssh" \
      && printf '%s\n' "$u" > "/etc/ssh/auth_principals/$u"; \
    done \
 && printf 'sre ALL=(ALL) ALL\n' > /etc/sudoers.d/sre \
 && chmod 440 /etc/sudoers.d/sre

COPY vendor/step /usr/local/bin/step
COPY ssh-target/sshd_config /etc/ssh/sshd_config
COPY ssh-target/entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/step /entrypoint.sh \
 && ssh-keygen -A \
 && rm -f /etc/ssh/ssh_host_rsa_key /etc/ssh/ssh_host_rsa_key.pub \
           /etc/ssh/ssh_host_ecdsa_key /etc/ssh/ssh_host_ecdsa_key.pub \
           /etc/ssh/ssh_host_ed25519_key-cert.pub

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
