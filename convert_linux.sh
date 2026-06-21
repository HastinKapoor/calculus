#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IN_DIR="$ROOT_DIR/litmus-tests"
OUT_DIR="$ROOT_DIR/litmus"
PY_SCRIPT="$ROOT_DIR/scripts/linux_to_calculus.py"

if [ ! -x "$(command -v python3)" ]; then
  echo "python3 not found" >&2
  exit 1
fi

if [ ! -f "$PY_SCRIPT" ]; then
  echo "Converter script not found: $PY_SCRIPT" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

shopt -s nullglob
for src in "$IN_DIR"/*.litmus; do
  [ -e "$src" ] || continue
  base="$(basename "$src")"
  echo "Converting: $base"
  python3 "$PY_SCRIPT" "$src" -o "$OUT_DIR/$base" || {
    echo "Conversion failed for $base" >&2
  }
done

echo "Conversion complete. Outputs in: $OUT_DIR"
