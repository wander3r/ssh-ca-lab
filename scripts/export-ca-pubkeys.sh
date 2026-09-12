#!/usr/bin/env bash
# Copy user/host CA public keys from the control-plane container into ansible/files.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CA="${CA_CONTAINER:-sshca-step-ca}"
OUT="$ROOT/ansible/files"
mkdir -p "$OUT" "$OUT/host-certs"
docker exec "$CA" cat /home/step/certs/ssh_user_ca_key.pub >"$OUT/ssh_user_ca_key.pub"
docker exec "$CA" cat /home/step/certs/ssh_host_ca_key.pub >"$OUT/ssh_host_ca_key.pub"
chmod 644 "$OUT/ssh_user_ca_key.pub" "$OUT/ssh_host_ca_key.pub"
echo "wrote $OUT/ssh_user_ca_key.pub"
echo "wrote $OUT/ssh_host_ca_key.pub"
echo "client known_hosts:"
echo "@cert-authority * $(tr -d '\r\n' < "$OUT/ssh_host_ca_key.pub")"
