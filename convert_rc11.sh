#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <rc11_input_dir> <output_dir>" >&2
  exit 2
fi

IN_DIR="$(cd "$1" && pwd)"
OUT_DIR="$(mkdir -p "$2" && cd "$2" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="$SCRIPT_DIR/rc11_to_calculus.py"

if [ ! -d "$IN_DIR" ]; then
  echo "Input directory not found: $IN_DIR" >&2
  exit 1
fi

if [ ! -f "$PY_SCRIPT" ]; then
  echo "Converter script not found: $PY_SCRIPT" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not found in PATH" >&2
  exit 1
fi

shopt -s nullglob
for src in "$IN_DIR"/*.c; do
  base="$(basename "$src" .c)"
  out="$OUT_DIR/$base.litmus"
  echo "Converting $src -> $out"
  python3 "$PY_SCRIPT" "$src" -o "$out" || {
    echo "Conversion failed for $src" >&2
  }
done

echo "Conversion complete. Outputs written to: $OUT_DIR"