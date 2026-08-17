#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 <input_dir> <output_dir> --kind <c|linux>" >&2
  exit 2
fi

if [ ! -d "$1" ]; then
  echo "Input directory not found: $1" >&2
  exit 1
fi

if [ "$3" != "--kind" ] || [ "$4" != "c" ] && [ "$4" != "linux" ]; then
  echo "Usage: $0 <input_dir> <output_dir> --kind <c|linux>" >&2
  exit 2
fi

IN_DIR="$(cd "$1" && pwd)"
mkdir -p "$2"
OUT_DIR="$(cd "$2" && pwd)"
KIND="$4"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="$SCRIPT_DIR/toggle_memorder_source_strength.py"

if [ ! -f "$PY_SCRIPT" ]; then
  echo "Generator script not found: $PY_SCRIPT" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not found in PATH" >&2
  exit 1
fi

mapfile -d '' -t sources < <(find "$IN_DIR" -type f -name '*.litmus' -print0 | LC_ALL=C sort -z)

if [ "${#sources[@]}" -eq 0 ]; then
  echo "No .litmus files found under: $IN_DIR" >&2
  exit 0
fi

for src in "${sources[@]}"; do
  rel_path="${src#$IN_DIR/}"
  rel_dir="$(dirname "$rel_path")"
  base="$(basename "$src" .litmus)"
  if [ "$rel_dir" = "." ]; then
    dest_dir="$OUT_DIR/$base"
  else
    dest_dir="$OUT_DIR/$rel_dir/$base"
  fi
  mkdir -p "$dest_dir"
  python3 "$PY_SCRIPT" "$src" "$dest_dir" --kind "$KIND"
done
