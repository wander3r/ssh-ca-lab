#!/usr/bin/env bash
# Issue a short-lived SSH user certificate via the portal API.
# Usage: scripts/issue-cert.sh /path/to/id_ed25519.pub [out-cert-path]
set -euo pipefail

PUBKEY_FILE=${1:?usage: issue-cert.sh <pubkey-file> [out-cert]}
OUT=${2:-"${PUBKEY_FILE%.pub}-cert.pub"}
PORTAL_URL=${PORTAL_URL:-http://127.0.0.1:8080}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PORTAL_USER=$(awk -F= '/^PORTAL_USER=/{print $2}' "$ROOT/credentials.txt" | tr -d '\r')
PORTAL_PASSWORD=$(awk -F= '/^PORTAL_PASSWORD=/{print $2}' "$ROOT/credentials.txt" | tr -d '\r')

curl -fsS -X POST "$PORTAL_URL/api/issue" \
  -F "username=${PORTAL_USER}" \
  -F "password=${PORTAL_PASSWORD}" \
  -F "pubkey=$(cat "$PUBKEY_FILE")" \
  -o /tmp/ssh-ca-lab-issue.json

python3 - "$OUT" <<'PY'
import json, sys
out = sys.argv[1]
with open("/tmp/ssh-ca-lab-issue.json") as f:
    j = json.load(f)
with open(out, "w") as f:
    f.write(j["certificate"].rstrip() + "\n")
sys.stderr.write(j.get("inspect", "") + "\n")
sys.stderr.write(f"wrote {out}\n")
PY

echo "$OUT"
