#!/bin/sh
set -eu

command -v openssl >/dev/null 2>&1 || {
  echo "openssl is required" >&2
  exit 1
}

printf 'SUBSCRIPTION_TOKEN=%s\n' "$(openssl rand -hex 32)"
printf 'ADMIN_TOKEN=%s\n' "$(openssl rand -hex 32)"
