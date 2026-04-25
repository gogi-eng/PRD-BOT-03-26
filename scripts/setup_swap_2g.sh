#!/usr/bin/env bash
# Idempotent: create 2G swap at /swapfile if no swap (or only tiny swap). Run with sudo.
# Safe for DO 1GB droplets; reduces OOM kills of trading bots.
set -euo pipefail
if [[ ${EUID:-0} -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi
if swapon --show 2>/dev/null | grep -q .; then
  echo "Swap already active:"
  swapon --show
  free -h
  exit 0
fi
if [[ -f /swapfile ]]; then
  if file /swapfile 2>/dev/null | grep -q "swap file"; then
    echo "Enabling existing /swapfile"
    swapon /swapfile
    free -h
    exit 0
  fi
fi
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
if ! grep -qF '/swapfile' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon --show
free -h
echo "OK: 2G swap on /swapfile"
