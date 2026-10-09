#!/usr/bin/env bash
# Install the integration's requirements from manifest.json, plus any packages given as
# arguments. A library version released minutes ago may not be on PyPI yet (when both
# repos are pushed together), so wait up to 15 minutes for the requirements to appear.
set -euo pipefail
mapfile -t REQS < <(jq -r '.requirements[]' custom_components/wifi_association_presence/manifest.json)
for i in $(seq 1 30); do
  if pip download --no-deps --no-cache-dir --quiet --dest /tmp/requirements-check "${REQS[@]}" >/dev/null 2>&1; then
    break
  fi
  if [ "$i" = 30 ]; then
    echo "::error::not on PyPI after 15 minutes: ${REQS[*]}"
    exit 1
  fi
  echo "Waiting for ${REQS[*]} to appear on PyPI ($i/30)..."
  sleep 30
done
pip install "$@" "${REQS[@]}"
