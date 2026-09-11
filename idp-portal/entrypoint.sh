#!/bin/bash
set -euo pipefail

ROOT_CA="${ROOT_CA_PATH:-/home/step/certs/root_ca.crt}"
CA_URL="${STEP_CA_URL:-https://step-ca:9000}"

echo "[portal] waiting for root CA at ${ROOT_CA} ..."
for i in $(seq 1 90); do
  if [ -s "${ROOT_CA}" ]; then
    break
  fi
  sleep 1
done
if [ ! -s "${ROOT_CA}" ]; then
  echo "[portal] ERROR: root CA not ready" >&2
  exit 1
fi

export STEPPATH="${STEPPATH:-/home/step-client}"
mkdir -p "${STEPPATH}/certs" "${STEPPATH}/config"

FP=$(step certificate fingerprint "${ROOT_CA}")
echo "[portal] CA fingerprint: ${FP}"

# Bootstrap step client against lab CA (idempotent)
if [ ! -f "${STEPPATH}/config/defaults.json" ]; then
  step ca bootstrap --ca-url "${CA_URL}" --fingerprint "${FP}" --force
fi

# Wait until CA HTTPS is up
for i in $(seq 1 60); do
  if step ca health --ca-url "${CA_URL}" --root "${ROOT_CA}" >/dev/null 2>&1; then
    echo "[portal] step-ca healthy"
    break
  fi
  sleep 1
done

echo "[portal] starting FastAPI on :8080"
exec uvicorn app:app --host 0.0.0.0 --port 8080
