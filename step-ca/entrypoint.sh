#!/bin/bash
set -euo pipefail
export STEPPATH="${STEPPATH:-/home/step}"
mkdir -p "${STEPPATH}/secrets" "${STEPPATH}/config" "${STEPPATH}/certs" "${STEPPATH}/db"

PW_FILE="${STEPPATH}/secrets/password"
if [ -n "${DOCKER_STEPCA_INIT_PASSWORD:-}" ]; then
  printf '%s' "${DOCKER_STEPCA_INIT_PASSWORD}" > "${PW_FILE}"
  chmod 600 "${PW_FILE}"
fi

if [ ! -f "${STEPPATH}/config/ca.json" ]; then
  if [ ! -s "${PW_FILE}" ]; then
    echo "[step-ca] ERROR: missing DOCKER_STEPCA_INIT_PASSWORD" >&2
    exit 1
  fi
  DNS_ARGS=()
  IFS=',' read -ra NAMES <<< "${DOCKER_STEPCA_INIT_DNS_NAMES:-localhost}"
  for n in "${NAMES[@]}"; do
    n="${n#"${n%%[![:space:]]*}"}"
    n="${n%"${n##*[![:space:]]}"}"
    [ -n "$n" ] && DNS_ARGS+=(--dns "$n")
  done
  SSH_FLAG=()
  case "${DOCKER_STEPCA_INIT_SSH:-}" in
    true|TRUE|1|yes|YES) SSH_FLAG+=(--ssh) ;;
  esac
  echo "[step-ca] initializing PKI (ssh=${DOCKER_STEPCA_INIT_SSH:-false})"
  step ca init \
    --deployment-type standalone \
    --name "${DOCKER_STEPCA_INIT_NAME:-SSH Lab CA}" \
    "${DNS_ARGS[@]}" \
    --address "${DOCKER_STEPCA_INIT_ADDRESS:-:9000}" \
    --provisioner "${DOCKER_STEPCA_INIT_PROVISIONER_NAME:-admin}" \
    --password-file "${PW_FILE}" \
    --provisioner-password-file "${PW_FILE}" \
    "${SSH_FLAG[@]}"
fi

echo "[step-ca] starting on ${DOCKER_STEPCA_INIT_ADDRESS:-:9000}"
exec step-ca --password-file "${PW_FILE}" "${STEPPATH}/config/ca.json"
