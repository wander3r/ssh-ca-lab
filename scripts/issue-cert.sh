#!/usr/bin/env bash
# Issue a short-lived SSH user certificate via the portal API.
# Usage:
#   scripts/issue-cert.sh /path/to/id_ed25519.pub [out-cert-path]
# Env:
#   PORTAL_URL, ISSUE_USER, ISSUE_PASSWORD  (override credentials.txt)
set -euo pipefail

PUBKEY_FILE=${1:?usage: issue-cert.sh <pubkey-file> [out-cert]}
OUT=${2:-"${PUBKEY_FILE%.pub}-cert.pub"}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CRED="$ROOT/credentials.txt"

cred() {
  local k="$1"
  [ -f "$CRED" ] || return 0
  awk -F= -v k="$k" '$1==k{print $2; exit}' "$CRED" | tr -d '\r'
}

PORTAL_URL=${PORTAL_URL:-$(cred PORTAL_URL)}
PORTAL_URL=${PORTAL_URL:-http://127.0.0.1:8088}
ISSUE_USER=${ISSUE_USER:-${PORTAL_USER:-$(cred PORTAL_USER)}}
ISSUE_PASSWORD=${ISSUE_PASSWORD:-${PORTAL_PASSWORD:-$(cred PORTAL_PASSWORD)}}
if [ -z "$ISSUE_USER" ] || [ -z "$ISSUE_PASSWORD" ]; then
  echo "set ISSUE_USER and ISSUE_PASSWORD (or PORTAL_USER/PORTAL_PASSWORD in credentials.txt)" >&2
  exit 1
fi

TMP_JSON=$(mktemp)
trap 'rm -f "$TMP_JSON"' EXIT

curl -fsS -X POST "$PORTAL_URL/api/issue" \
  -F "username=${ISSUE_USER}" \
  -F "password=${ISSUE_PASSWORD}" \
  -F "pubkey=$(cat "$PUBKEY_FILE")" \
  -o "$TMP_JSON"

python3 - "$OUT" "$TMP_JSON" <<'PY'
import json, sys
out, path = sys.argv[1], sys.argv[2]
with open(path) as f:
    j = json.load(f)
with open(out, "w") as f:
    f.write(j["certificate"].rstrip() + "\n")
sys.stderr.write(j.get("inspect", "") + "\n")
unix = j.get("unix_user") or ",".join(j.get("principals") or [])
role = j.get("role") or ""
sudo = j.get("sudo")
sys.stderr.write(f"wrote {out}  role={role} unix_user={unix} sudo={sudo}\n")
PY
echo "$OUT"
