#!/usr/bin/env bash
# Install the integration's requirements from manifest.json, plus any packages given as
# arguments. A library version released minutes ago may not be on PyPI yet (when both
# repos are pushed together), and PyPI's CDN servers pick up a new release a few minutes
# apart: a check that finds it can be followed by an install that doesn't. So retry the
# install itself, for up to 15 minutes, but only while the requirement isn't found;
# any other install error fails at once.
set -euo pipefail
mapfile -t REQS < <(jq -r '.requirements[]' custom_components/wifi_association_presence/manifest.json)
for i in $(seq 1 30); do
  if output=$(pip install --no-cache-dir "$@" "${REQS[@]}" 2>&1); then
    echo "$output" | tail -n 3
    exit 0
  fi
  echo "$output" | tail -n 5
  if ! grep -qE "No matching distribution|Could not find a version" <<<"$output"; then
    echo "::error::installing the requirements failed"
    exit 1
  fi
  if [ "$i" = 30 ]; then
    echo "::error::not on PyPI after 15 minutes: ${REQS[*]}"
    exit 1
  fi
  echo "Waiting for ${REQS[*]} to appear on PyPI ($i/30)..."
  sleep 30
done
