#!/usr/bin/env bash
set -euo pipefail

# Runs calculus_test.py for every .litmus file in the specified litmus directory
# Usage: ./run_all_litmus.sh [litmus_directory]
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_LITMUS_DIR="$DIR/litmus"

# Accept an optional first argument as the litmus directory
LITMUS_DIR="${1:-$DEFAULT_LITMUS_DIR}"

if [ ! -d "$LITMUS_DIR" ]; then
  echo "No litmus directory at $LITMUS_DIR" >&2
  exit 1
fi

shopt -s nullglob
files=("$LITMUS_DIR"/*.litmus)
if [ ${#files[@]} -eq 0 ]; then
  echo "No .litmus files found in $LITMUS_DIR"
  exit 0
fi

for f in "${files[@]}"; do
  echo "=== Running: $f ==="
  python3 "$DIR/calculus_test.py" "$f" || {
    echo "calculus_test.py failed on $f" >&2
    # continue to next file rather than exiting
  }
done

echo "All done."
```# filepath: /home/kapoorh/Documents/calculus/run_all_litmus.sh
#!/usr/bin/env bash
set -euo pipefail

# Runs calculus_test.py for every .litmus file in the specified litmus directory
# Usage: ./run_all_litmus.sh [litmus_directory]
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_LITMUS_DIR="$DIR/litmus"

# Accept an optional first argument as the litmus directory
LITMUS_DIR="${1:-$DEFAULT_LITMUS_DIR}"

if [ ! -d "$LITMUS_DIR" ]; then
  echo "No litmus directory at $LITMUS_DIR" >&2
  exit 1
fi

shopt -s nullglob
files=("$LITMUS_DIR"/*.litmus)
if [ ${#files[@]} -eq 0 ]; then
  echo "No .litmus files found in $LITMUS_DIR"
  exit 0
fi

for f in "${files[@]}"; do
  echo "=== Running: $f ==="
  python3 "$DIR/calculus_test.py" "$f" || {
    echo "calculus_test.py failed on $f" >&2
    # continue to next file rather than exiting
  }
done

echo "All done."