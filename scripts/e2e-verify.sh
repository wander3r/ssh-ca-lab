#!/usr/bin/env bash
# End-to-end: generate key -> issue cert via portal -> SSH to target.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DOCKER_HOST="${DOCKER_HOST:-tcp://127.0.0.1:2375}"
WORKDIR="$ROOT/demo-client"
mkdir -p "$WORKDIR"
KEY="$WORKDIR/id_lab"
rm -f "$KEY" "$KEY.pub" "$KEY-cert.pub"

echo "==> generate ed25519 keypair"
ssh-keygen -t ed25519 -N "" -f "$KEY" -C "e2e@ssh-ca-lab"

echo "==> issue cert via portal API"
"$ROOT/scripts/issue-cert.sh" "$KEY.pub" "$KEY-cert.pub"

echo "==> inspect cert"
ssh-keygen -L -f "$KEY-cert.pub"

echo "==> SSH into target"
ssh -i "$KEY" \
  -o CertificateFile="$KEY-cert.pub" \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -p 2222 \
  demo@127.0.0.1 \
  'echo SUCCESS_SSH_CA_LAB; id; hostname; echo cert_ok'
