#!/usr/bin/env bash
# Request a short-lived SSH user certificate, then optionally SSH.
#
# Browser / Feishu (default):
#   ./scripts/ssh-ca-login.sh
#   ./scripts/ssh-ca-login.sh --ssh
#
# Local accounts (demo overlay only):
#   ./scripts/ssh-ca-login.sh --local sre
#   ./scripts/ssh-ca-login.sh --local dev --ssh
#
# Options:
#   -i KEY          identity file (default: demo-client/id_lab)
#   --local USER    POST /api/issue with USER (password = USER, or ISSUE_PASSWORD)
#   --ssh           ssh to the target after the cert is written
#   --no-open       print login URL only (do not open a browser)
#   --timeout SEC   poll timeout (default 300)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CRED="$ROOT/credentials.txt"
KEY="$ROOT/demo-client/id_lab"
LOCAL_USER=""
DO_SSH=0
OPEN_BROWSER=1
TIMEOUT=300

cred() {
  local k="$1"
  [ -f "$CRED" ] || return 0
  awk -F= -v k="$k" '$1==k{print $2; exit}' "$CRED" | tr -d '\r'
}

usage() { sed -n '2,20p' "$0" | sed 's/^# \?//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    -i) KEY="$2"; shift 2 ;;
    --local) LOCAL_USER="$2"; shift 2 ;;
    --ssh) DO_SSH=1; shift ;;
    --no-open) OPEN_BROWSER=0; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown arg: $1" >&2; usage 1 ;;
  esac
done

PORTAL_URL=${PORTAL_URL:-$(cred PORTAL_URL)}
PORTAL_URL=${PORTAL_URL:-http://127.0.0.1:8088}
SSH_HOST=${SSH_HOST:-$(cred SSH_HOST)}
SSH_HOST=${SSH_HOST:-127.0.0.1}
SSH_PORT=${SSH_PORT:-$(cred SSH_PORT)}
SSH_PORT=${SSH_PORT:-2222}

mkdir -p "$(dirname "$KEY")"
if [ ! -f "$KEY" ]; then
  echo "==> generating $KEY"
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C "sshca@$(hostname -s 2>/dev/null || echo sshca)"
fi
PUB="$KEY.pub"
CERT="${KEY}-cert.pub"
UNIX_USER=""
JSON_TMP=$(mktemp)
trap 'rm -f "$JSON_TMP"' EXIT

write_cert() {
  python3 - "$CERT" "$JSON_TMP" <<'PY'
import json, sys
out, path = sys.argv[1], sys.argv[2]
with open(path) as f:
    j = json.load(f)
if j.get("status") == "error":
    raise SystemExit(j.get("error") or "issue failed")
cert = j.get("certificate")
if not cert:
    raise SystemExit("no certificate in response: " + json.dumps(j)[:400])
with open(out, "w") as f:
    f.write(cert.rstrip() + "\n")
unix = j.get("unix_user") or ""
print(unix)
inspect = j.get("inspect") or ""
if inspect:
    sys.stderr.write(inspect if inspect.endswith("\n") else inspect + "\n")
sys.stderr.write(
    "wrote %s  role=%s unix_user=%s sudo=%s\n"
    % (out, j.get("role") or "", unix, j.get("sudo"))
)
PY
}

if [ -n "$LOCAL_USER" ]; then
  PASS="${ISSUE_PASSWORD:-$LOCAL_USER}"
  echo "==> issue via $PORTAL_URL/api/issue as $LOCAL_USER"
  curl -fsS -X POST "$PORTAL_URL/api/issue" \
    -F "username=${LOCAL_USER}" \
    -F "password=${PASS}" \
    -F "pubkey=$(cat "$PUB")" \
    -o "$JSON_TMP"
  UNIX_USER=$(write_cert)
else
  echo "==> begin client session"
  BEGIN=$(curl -fsS -X POST "$PORTAL_URL/api/client/begin" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"pubkey": open(sys.argv[1]).read()}))' "$PUB")")
  LOGIN_URL=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["login_url"])' <<<"$BEGIN")
  POLL_URL=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["poll_url"])' <<<"$BEGIN")
  echo "Open this URL, then log in (Feishu or local sre/dev):"
  echo "  $LOGIN_URL"
  if [ "$OPEN_BROWSER" -eq 1 ]; then
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$LOGIN_URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
      open "$LOGIN_URL" >/dev/null 2>&1 || true
    fi
  fi
  echo "==> waiting for login (timeout ${TIMEOUT}s)"
  START=$(date +%s)
  while true; do
    NOW=$(date +%s)
    if [ $((NOW - START)) -ge "$TIMEOUT" ]; then
      echo "timeout waiting for login" >&2
      exit 1
    fi
    RESP=$(curl -sS -w '\n%{http_code}' "$POLL_URL" || true)
    HTTP=$(printf '%s\n' "$RESP" | tail -n1)
    BODY=$(printf '%s\n' "$RESP" | sed '$d')
    if [ "$HTTP" != "200" ]; then
      sleep 1
      continue
    fi
    STATUS=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' <<<"$BODY")
    case "$STATUS" in
      ready)
        printf '%s\n' "$BODY" > "$JSON_TMP"
        UNIX_USER=$(write_cert)
        break
        ;;
      error)
        python3 -c 'import json,sys; raise SystemExit(json.load(sys.stdin).get("error","issue failed"))' <<<"$BODY"
        ;;
      pending) sleep 1 ;;
      *) sleep 1 ;;
    esac
  done
fi

echo
echo "ssh -i $KEY \\"
echo "  -o CertificateFile=$CERT \\"
echo "  -o IdentitiesOnly=yes \\"
echo "  -p $SSH_PORT ${UNIX_USER:-sre}@$SSH_HOST"

if [ "$DO_SSH" -eq 1 ]; then
  exec ssh -i "$KEY" \
    -o CertificateFile="$CERT" \
    -o IdentitiesOnly=yes \
    -p "$SSH_PORT" \
    "${UNIX_USER:-sre}@${SSH_HOST}"
fi
