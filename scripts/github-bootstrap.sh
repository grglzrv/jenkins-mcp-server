#!/usr/bin/env bash
set -euo pipefail

OWNER="${GITHUB_OWNER:-grglzrv}"
REPO="${GITHUB_REPO:-jenkins-mcp-server}"
VISIBILITY="${GITHUB_VISIBILITY:-public}"

command -v gh >/dev/null 2>&1 || {
  echo "GitHub CLI is required: https://cli.github.com/" >&2
  exit 1
}

gh auth status >/dev/null 2>&1 || gh auth login --hostname github.com --git-protocol ssh --web

git init -b main 2>/dev/null || true
git add .
if ! git diff --cached --quiet; then
  git commit -m "feat: publish Jenkins MCP server v$(cat VERSION)"
fi

if gh repo view "${OWNER}/${REPO}" >/dev/null 2>&1; then
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "git@github.com:${OWNER}/${REPO}.git"
  git push -u origin main
else
  gh repo create "${OWNER}/${REPO}" \
    "--${VISIBILITY}" \
    --description "Production-ready Jenkins MCP server for Hermes Agent" \
    --source . \
    --remote origin \
    --push
fi

cat <<MSG
Repository pushed to https://github.com/${OWNER}/${REPO}

Next:
  1. Open Settings > Actions > General and allow GitHub Actions.
  2. Confirm Workflow permissions permit package publishing.
  3. Push tag v$(cat VERSION) to publish the image and Helm chart:
       git tag -a v$(cat VERSION) -m "Release v$(cat VERSION)"
       git push origin v$(cat VERSION)
MSG
