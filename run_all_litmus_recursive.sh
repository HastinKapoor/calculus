#!/usr/bin/env bash
set -euo pipefail

# Runs calculus_test.py for every .litmus file in the specified litmus directory and all its subfolders
# Usage: ./run_all_litmus.sh [litmus_root_directory]
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_LITMUS_DIR="$DIR/litmus"

LITMUS_ROOT="${1:-$DEFAULT_LITMUS_DIR}"

if [ ! -d "$LITMUS_ROOT" ]; then
  echo "No litmus root directory at $LITMUS_ROOT" >&2
  exit 1
fi

# collect files recursively (handles spaces/newlines)
readarray -d '' -t files < <(find "$LITMUS_ROOT" -type f -name '*.litmus' -print0)

if [ ${#files[@]} -eq 0 ]; then
  echo "No .litmus files found under $LITMUS_ROOT"
  exit 0
fi

for f in "${files[@]}"; do
  echo "=== Running: $f ==="
  python3 "$DIR/calculus_test.py" "$f" || {
    echo "calculus_test.py failed on $f" >&2
    # continue to next file
  }
done

echo "All done."