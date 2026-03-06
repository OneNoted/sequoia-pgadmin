#!/usr/bin/env sh
set -eu

host_port="${PGADMIN_HOST_PORT:-5050}"
target="http://127.0.0.1:${host_port}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale is not installed on this host" >&2
  exit 1
fi

if ! tailscale status >/dev/null 2>&1; then
  echo "tailscale is not connected; run 'sudo tailscale up' first" >&2
  exit 1
fi

tailscale serve --bg --yes "${target}"
tailscale serve status
