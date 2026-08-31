#!/usr/bin/env bash
# Install a pinned shellcheck binary for CI.
#
# Why not `scripts/ci-apt.sh shellcheck`: apt on ubuntu-24.04 resolves 0.9.0
# and is unpinned across image/archive updates, while the zero-excludes
# runtime lint pass (#1175) was verified against 0.11.0. Same reasoning as
# the ruff pin in ci.yml — a future release must not widen rules and turn CI
# red without an explicit bump here. The official static binary from the
# upstream release is pinned by exact version AND sha256, so neither the
# mirror nor the tag can drift silently.
#
# Used by both ci.yml jobs that run shellcheck (the `shellcheck` job, and
# `test` via run-tests.sh step 9) so the two blocking jobs cannot disagree —
# mirroring how ruff is pinned to one version in both of its jobs.
# /usr/local/bin precedes /usr/bin on the runner PATH, so this also shadows
# the runner image's preinstalled 0.9.0.
set -euo pipefail

SC_VERSION="v0.11.0"
SC_SHA256="8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198"
SC_URL="https://github.com/koalaman/shellcheck/releases/download/${SC_VERSION}/shellcheck-${SC_VERSION}.linux.x86_64.tar.xz"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Bounded, retried transfer — same spirit as ci-apt.sh's phase bound (#1046/
# #1049): a stalled CDN must fail fast and get retried, not eat the job budget.
# --max-time bounds ONE attempt and curl resets it on every retry, so without
# --retry-max-time the four attempts plus their delays reach 8m15s — over half
# the 15-minute test-job budget this step shares with the suite itself.
curl -fsSL --retry 3 --retry-delay 5 --retry-max-time 180 --max-time 120 \
  -o "$tmp/shellcheck.tar.xz" "$SC_URL"
echo "${SC_SHA256}  $tmp/shellcheck.tar.xz" | sha256sum -c -

tar -xJf "$tmp/shellcheck.tar.xz" -C "$tmp"
sudo install -m 0755 "$tmp/shellcheck-${SC_VERSION}/shellcheck" /usr/local/bin/shellcheck

shellcheck --version
