#!/bin/sh
# Optional helper: wait until SSH user CA pubkey exists (for healthchecks/scripts).
set -e
for i in $(seq 1 60); do
  if [ -f /home/step/certs/ssh_user_ca_key.pub ]; then
    echo "ssh_user_ca_key.pub ready"
    exit 0
  fi
  sleep 1
done
echo "timeout waiting for ssh_user_ca_key.pub" >&2
exit 1
