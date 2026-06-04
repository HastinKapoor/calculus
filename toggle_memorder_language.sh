#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <input_dir> <output_dir>" >&2
  exit 2
fi

IN_DIR="$(cd "$1" && pwd)"
OUT_DIR="$(mkdir -p "$2" && cd "$2" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="$SCRIPT_DIR/toggle_memorder_language.py"

if [ ! -d "$IN_DIR" ]; then
  echo "Input directory not found: $IN_DIR" >&2
  exit 1
fi
if [ ! -f "$PY_SCRIPT" ]; then
  echo "Toggle script not found: $PY_SCRIPT" >&2
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

  if [ "$rel_dir" = "." ]; then
    dest_dir="$OUT_DIR"
  else
    dest_dir="$OUT_DIR/$rel_dir"
  fi

  mkdir -p "$dest_dir"
  echo "Processing $src -> $dest_dir/"
  python3 "$PY_SCRIPT" "$src" "$dest_dir" || {
    echo "Toggle generation failed for $src" >&2
  }
done

echo "All files processed. Outputs in: $OUT_DIR"
