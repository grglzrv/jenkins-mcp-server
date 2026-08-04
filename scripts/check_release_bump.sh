#!/usr/bin/env bash
# Fail when a change that ships to users lands without a VERSION bump.
set -euo pipefail
base="$1"
changed="$(git diff --name-only "$base" HEAD)"

# Paths whose contents reach users through a published artifact.
releasable="$(printf '%s\n' "$changed" | grep -E '^(src/|charts/jenkins-mcp-server/|docker/)' || true)"
bumped="$(printf '%s\n' "$changed" | grep -x 'VERSION' || true)"

if [ -n "$releasable" ] && [ -z "$bumped" ]; then
  echo "These files ship to users but VERSION was not bumped:"
  printf '  %s\n' $releasable
  echo
  echo "The chart takes its image tag from appVersion, which follows VERSION, so"
  echo "an unbumped change is never published: the release workflow triggers on a"
  echo "VERSION change, and the existing tag would otherwise be overwritten."
  echo
  echo "Run: make version VERSION=X.Y.Z"
  exit 1
fi
echo "no unpublished changes"
