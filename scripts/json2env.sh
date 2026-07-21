#!/bin/bash
# json2env.sh
#
# Converts a flat JSON object (read from stdin) into KEY=VALUE lines,
# one per line - the format systemd's EnvironmentFile= directive and
# `export $(cat .env | xargs)` both expect.
#
# Usage:
#   curl ... | jq '.data.data' | ./json2env.sh > .env
#
# Input example (stdin):
#   {"DB_HOST": "localhost", "DB_PORT": "5432"}
#
# Output (stdout):
#   DB_HOST=localhost
#   DB_PORT=5432
#
# Matches the calling convention already used by the elevate services'
# deployment playbooks, so this repo's Ansible fetch task can pipe Vault's
# JSON response straight through it the same way.

set -euo pipefail

# `to_entries` turns {"K":"V"} into [{"key":"K","value":"V"}, ...]
# `map(...)` formats each entry as a literal "KEY=VALUE" string
# `.[]` unwraps the array so jq -r prints one line per entry, unquoted
jq -r 'to_entries | map("\(.key)=\(.value)") | .[]'
