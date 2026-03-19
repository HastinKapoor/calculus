#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <input_dir> <output_dir>" >&2
  exit 2
fi

IN_DIR="$(cd "$1" && pwd)"
OUT_DIR="$(mkdir -p "$2" && cd "$2" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="$SCRIPT_DIR/toggle_memorder_strength.py"
MANIFEST="$OUT_DIR/deduped_variants.tsv"

if [ ! -d "$IN_DIR" ]; then
  echo "Input directory not found: $IN_DIR" >&2
  exit 1
fi
if [ ! -f "$PY_SCRIPT" ]; then
  echo "Generator script not found: $PY_SCRIPT" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not found in PATH" >&2
  exit 1
fi

declare -A seen_variants
printf "duplicate_variant\trepresentative_variant\n" > "$MANIFEST"

mapfile -t sources < <(find "$IN_DIR" -maxdepth 1 -type f -name '*.litmus' | LC_ALL=C sort)

if [ "${#sources[@]}" -eq 0 ]; then
  echo "No .litmus files found in: $IN_DIR" >&2
  exit 0
fi

for src in "${sources[@]}"; do
  base="$(basename "$src" .litmus)"
  dest_dir="$OUT_DIR/$base"
  tmp_dir="$(mktemp -d)"
  mkdir -p "$dest_dir"
  echo "Processing $src -> $dest_dir/"
  python3 "$PY_SCRIPT" "$src" "$tmp_dir"

  mapfile -t generated < <(find "$tmp_dir" -maxdepth 1 -type f -name '*.litmus' | LC_ALL=C sort)
  for generated_file in "${generated[@]}"; do
    digest="$(python3 "$PY_SCRIPT" --canonical "$generated_file")"
    rel_dest="$base/$(basename "$generated_file")"
    if [ -n "${seen_variants[$digest]:-}" ]; then
      printf "%s\t%s\n" "$rel_dest" "${seen_variants[$digest]}" >> "$MANIFEST"
      continue
    fi
    seen_variants[$digest]="$rel_dest"
    mv "$generated_file" "$dest_dir/"
  done

  rm -rf "$tmp_dir"
done

echo "All files processed. Outputs in: $OUT_DIR"
echo "Deduplication manifest: $MANIFEST"
