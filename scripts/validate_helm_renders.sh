#!/usr/bin/env bash
set -euo pipefail

chart="${1:-charts/jenkins-mcp-server}"
render_dir=$(mktemp -d)
trap 'rm -rf "$render_dir"' EXIT

render_and_validate() {
  local name=$1
  shift
  local output="$render_dir/$name.yaml"

  helm template jenkins-mcp "$chart" --namespace jenkins-mcp "$@" > "$output"
  kubeconform \
    -strict \
    -summary \
    -ignore-missing-schemas \
    -kubernetes-version 1.33.0 \
    "$output"
}

render_and_validate default \
  --set jenkins.url=https://jenkins.example.com \
  --set jenkins.credentials.create.jenkinsUserId=ci \
  --set jenkins.credentials.create.jenkinsApiToken=ci-placeholder

for values_file in examples/values/*.yaml; do
  name=$(basename "$values_file" .yaml)
  echo "Validating $values_file"
  render_and_validate "$name" \
    -f "$values_file" \
    --set jenkins.credentials.create.jenkinsApiToken=ci-placeholder
done
