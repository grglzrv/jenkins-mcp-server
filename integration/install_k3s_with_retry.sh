#!/bin/sh

# Retry the complete idempotent installer because it downloads the k3s binary
# and checksum itself; retrying only get.k3s.io does not cover those assets.
set -eu

version=${1:?usage: install_k3s_with_retry.sh K3S_VERSION}
installer=${RUNNER_TEMP:-/tmp}/install-k3s.sh
log=${RUNNER_TEMP:-/tmp}/install-k3s.log

attempt=1
while [ "$attempt" -le 5 ]; do
    if {
        curl --fail --silent --show-error --location \
            --retry 3 --retry-all-errors --retry-delay 2 \
            --output "$installer" https://get.k3s.io \
            && INSTALL_K3S_VERSION="$version" \
                INSTALL_K3S_EXEC='--disable traefik --disable metrics-server' \
                sh "$installer"
    } >"$log" 2>&1; then
        cat "$log"
        exit 0
    fi
    cat "$log" >&2

    # Do not hide a deterministic installer or service-start failure.
    grep -Eqi '\[ERROR\][[:space:]]+Download failed|curl: \((6|7|22|28|35|56)\)|unexpected EOF|connection reset' \
        "$log" || exit 1
    [ "$attempt" -lt 5 ] || exit 1

    delay=$((attempt * 10))
    echo "k3s ${version} attempt ${attempt} failed; retrying in ${delay}s" >&2
    sleep "$delay"
    attempt=$((attempt + 1))
done
