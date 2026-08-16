#!/usr/bin/env bash
set -euo pipefail

mode=${1:-}
case "$mode" in
  write|verify) ;;
  *) echo "usage: $0 write|verify" >&2; exit 2 ;;
esac

python_minor=$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
platform=$(python -c 'import sys; print(sys.platform)')
if [[ "$python_minor" != "3.12" || "$platform" != "linux" ]]; then
  echo "release dependency locks require Python 3.12 on Linux" >&2
  echo "current environment: Python $python_minor on $platform" >&2
  exit 1
fi

lock_pip_version=${LOCK_PIP_VERSION:-26.1.2}
pip_tools_version=${PIP_TOOLS_VERSION:-7.6.1}
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

python -m venv "$workdir/venv"
"$workdir/venv/bin/python" -m pip install --quiet --upgrade \
  "pip==$lock_pip_version" \
  "pip-tools==$pip_tools_version"
pip_compile="$workdir/venv/bin/pip-compile"

compile() {
  local input=$1
  local output=$2
  CUSTOM_COMPILE_COMMAND="make lock" "$pip_compile" \
    --quiet \
    --generate-hashes \
    --strip-extras \
    --output-file="$output" \
    "$input"
}

if [[ "$mode" == "write" ]]; then
  mkdir -p requirements
  compile pyproject.toml requirements/runtime.txt
  compile requirements/build.in requirements/build.txt
  exit 0
fi

compile pyproject.toml "$workdir/runtime.txt"
compile requirements/build.in "$workdir/build.txt"
diff -u requirements/runtime.txt "$workdir/runtime.txt"
diff -u requirements/build.txt "$workdir/build.txt"
