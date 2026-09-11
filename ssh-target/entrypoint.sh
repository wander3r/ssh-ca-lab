#!/bin/bash
set -euo pipefail

SRC="${CA_USER_PUBKEY:-/home/step/certs/ssh_user_ca_key.pub}"
DEST=/ca-keys/ssh_user_ca_key.pub
mkdir -p /ca-keys

echo "[ssh-target] waiting for CA user pubkey at ${SRC} ..."
for i in $(seq 1 90); do
  if [ -s "${SRC}" ]; then
    cp "${SRC}" "${DEST}"
    chmod 644 "${DEST}"
    echo "[ssh-target] installed TrustedUserCAKeys -> ${DEST}"
    cat "${DEST}"
    break
  fi
  sleep 1
done

if [ ! -s "${DEST}" ]; then
  echo "[ssh-target] ERROR: CA pubkey not found at ${SRC}" >&2
  ls -la /home/step/certs 2>/dev/null || true
  exit 1
fi

echo "[ssh-target] starting sshd on :22 (user=demo, certificate auth only)"
exec /usr/sbin/sshd -D -e
