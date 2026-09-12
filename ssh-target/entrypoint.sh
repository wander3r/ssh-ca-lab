#!/bin/bash
set -euo pipefail

SRC="${CA_USER_PUBKEY:-/home/step/certs/ssh_user_ca_key.pub}"
DEST=/ca-keys/ssh_user_ca_key.pub
ROOT_CA="${ROOT_CA_PATH:-/home/step/certs/root_ca.crt}"
CA_URL="${STEP_CA_URL:-https://step-ca:9000}"
HOST_KEY=/etc/ssh/ssh_host_ed25519_key
HOST_CERT="${HOST_KEY}-cert.pub"
mkdir -p /ca-keys

echo "[ssh-target] waiting for CA user pubkey at ${SRC} ..."
for i in $(seq 1 90); do
  if [ -s "${SRC}" ] && [ -s "${ROOT_CA}" ]; then
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

if [ -n "${SRE_PASSWORD:-}" ]; then
  echo "sre:${SRE_PASSWORD}" | chpasswd
  echo "[ssh-target] sre Unix password set (sudo will prompt; SSH still cert-only)"
else
  echo "[ssh-target] WARNING: SRE_PASSWORD unset; sudo as sre cannot succeed" >&2
fi

if [ -z "${PROVISIONER_PASSWORD:-}" ]; then
  echo "[ssh-target] ERROR: PROVISIONER_PASSWORD required to sign host certificate" >&2
  exit 1
fi

export STEPPATH="${STEPPATH:-/tmp/step-client}"
mkdir -p "${STEPPATH}/certs" "${STEPPATH}/config"
FP=$(step certificate fingerprint "${ROOT_CA}")
echo "[ssh-target] CA fingerprint: ${FP}"

boot_ok=0
for i in $(seq 1 60); do
  if step ca bootstrap --ca-url "${CA_URL}" --fingerprint "${FP}" --force >/dev/null; then
    boot_ok=1
    break
  fi
  sleep 1
done
if [ "$boot_ok" -ne 1 ]; then
  echo "[ssh-target] ERROR: step ca bootstrap failed" >&2
  exit 1
fi

PASS=$(mktemp)
printf '%s' "${PROVISIONER_PASSWORD}" > "${PASS}"
chmod 600 "${PASS}"
cleanup() { rm -f "${PASS}"; }
trap cleanup EXIT

HOST_ID="${SSH_HOST_IDENTITY:-ssh-target}"
cmd=(
  step ssh certificate "${HOST_ID}" "${HOST_KEY}.pub"
  --host --sign
  --provisioner "${PROVISIONER_NAME:-admin}"
  --provisioner-password-file "${PASS}"
  --ca-url "${CA_URL}"
  --root "${ROOT_CA}"
  --not-after "${HOST_CERT_TTL:-720h}"
  --force
)
IFS=',' read -ra PS <<< "${SSH_HOST_PRINCIPALS:-ssh-target,localhost,127.0.0.1}"
for p in "${PS[@]}"; do
  p="${p#"${p%%[![:space:]]*}"}"
  p="${p%"${p##*[![:space:]]}"}"
  [ -n "$p" ] && cmd+=(--principal "$p")
done

echo "[ssh-target] signing host certificate as ${HOST_ID}"
"${cmd[@]}"
if [ ! -s "${HOST_CERT}" ]; then
  echo "[ssh-target] ERROR: host certificate missing at ${HOST_CERT}" >&2
  exit 1
fi
chmod 644 "${HOST_CERT}"
ssh-keygen -L -f "${HOST_CERT}" || true

echo "[ssh-target] starting sshd on :22 (users=sre/dev/demo, user+host certs)"
exec /usr/sbin/sshd -D -e
