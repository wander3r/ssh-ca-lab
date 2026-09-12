#!/usr/bin/env bash
# End-to-end: generate key -> issue certs for sre/dev -> SSH (+ sudo check).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKDIR="$ROOT/demo-client"
mkdir -p "$WORKDIR"
KEY="$WORKDIR/id_lab"
rm -f "$KEY" "$KEY.pub" "$WORKDIR"/id_lab-*-cert.pub

cred() {
  awk -F= -v k="$1" '$1==k{print $2; exit}' "$ROOT/credentials.txt" | tr -d '\r'
}
export PORTAL_URL="${PORTAL_URL:-$(cred PORTAL_URL)}"
export PORTAL_URL="${PORTAL_URL:-http://127.0.0.1:8088}"
SSH_HOST="${SSH_HOST:-$(cred SSH_HOST)}"
SSH_HOST="${SSH_HOST:-127.0.0.1}"
SSH_PORT="${SSH_PORT:-$(cred SSH_PORT)}"
SSH_PORT="${SSH_PORT:-2222}"

SRE_PASSWORD="${SRE_PASSWORD:-$(cred SRE_PASSWORD)}"
SRE_PASSWORD="${SRE_PASSWORD:-sre}"

KNOWN_HOSTS="$WORKDIR/known_hosts"
if docker exec "${CA_CONTAINER:-sshca-step-ca}" cat /home/step/certs/ssh_host_ca_key.pub >"$WORKDIR/host_ca.pub" 2>/dev/null; then
  echo "@cert-authority * $(tr -d '\r\n' < "$WORKDIR/host_ca.pub")" > "$KNOWN_HOSTS"
  SSH_HOST_OPTS=(-o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KNOWN_HOSTS")
  echo "==> using host CA in $KNOWN_HOSTS"
else
  SSH_HOST_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
  echo "==> WARNING: could not read host CA; falling back to Trust On First Use"
fi

ssh_as() {
  local user="$1" cert="$2"
  shift 2
  ssh -i "$KEY" \
    -o CertificateFile="$cert" \
    -o IdentitiesOnly=yes \
    "${SSH_HOST_OPTS[@]}" \
    -p "$SSH_PORT" \
    "${user}@${SSH_HOST}" \
    "$@"
}

echo "==> generate ed25519 keypair"
ssh-keygen -t ed25519 -N "" -f "$KEY" -C "e2e@sshca"

echo "==> issue sre cert (sudo)"
ISSUE_USER=sre ISSUE_PASSWORD=sre \
  "$ROOT/scripts/issue-cert.sh" "$KEY.pub" "$WORKDIR/id_lab-sre-cert.pub" >/dev/null
echo "==> inspect sre cert"
ssh-keygen -L -f "$WORKDIR/id_lab-sre-cert.pub"
echo "==> SSH as sre; sudo must require a password"
ssh_as sre "$WORKDIR/id_lab-sre-cert.pub" 'echo SUCCESS_SSH_CA_LAB_SRE; id'
if ssh_as sre "$WORKDIR/id_lab-sre-cert.pub" 'sudo -n true' >/dev/null 2>&1; then
  echo "ERROR: sudo must not be NOPASSWD" >&2
  exit 1
fi
echo "SUDO_PASSWORD_REQUIRED_OK"
if printf '%s\n' "wrong-password" | ssh_as sre "$WORKDIR/id_lab-sre-cert.pub" 'sudo -S -p "" -k true' >/dev/null 2>&1; then
  echo "ERROR: sudo accepted a wrong password" >&2
  exit 1
fi
printf '%s\n' "$SRE_PASSWORD" | ssh_as sre "$WORKDIR/id_lab-sre-cert.pub" 'sudo -S -p "" -k true && echo SUDO_OK'

echo "==> issue dev cert (no sudo)"
ISSUE_USER=dev ISSUE_PASSWORD=dev \
  "$ROOT/scripts/issue-cert.sh" "$KEY.pub" "$WORKDIR/id_lab-dev-cert.pub" >/dev/null
echo "==> inspect dev cert"
ssh-keygen -L -f "$WORKDIR/id_lab-dev-cert.pub"
echo "==> SSH as dev, sudo must fail"
ssh_as dev "$WORKDIR/id_lab-dev-cert.pub" 'echo SUCCESS_SSH_CA_LAB_DEV; id'
if ssh_as dev "$WORKDIR/id_lab-dev-cert.pub" 'sudo -n true' 2>/dev/null; then
  echo "ERROR: dev must not have sudo" >&2
  exit 1
fi
echo "NO_SUDO_OK"

echo "==> demo login still maps to sre (lab alias)"
ISSUE_USER="$(cred PORTAL_USER)" ISSUE_PASSWORD="$(cred PORTAL_PASSWORD)" \
  "$ROOT/scripts/issue-cert.sh" "$KEY.pub" "$WORKDIR/id_lab-demo-cert.pub" >/dev/null
ssh_as sre "$WORKDIR/id_lab-demo-cert.pub" 'echo SUCCESS_SSH_CA_LAB_DEMO_ALIAS'

echo SUCCESS_SSH_CA_LAB
