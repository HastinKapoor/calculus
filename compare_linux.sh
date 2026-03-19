#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run_and_compare_dat3m_lkmm.sh [converted_dir] [original_dir]
# Defaults assume repository layout:
#   converted: ./converted/Dat3M/LKMM
#   original:  ./litmus/Dat3M/LKMM

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERTED_DIR="${1:-$ROOT/converted/Dat3M/LKMM}"
ORIGINAL_DIR="${2:-$ROOT/litmus/Dat3M/LKMM}"
CALCULUS_BIN="$ROOT/calculus_test.py"

if [ ! -d "$CONVERTED_DIR" ]; then
  echo "Converted directory not found: $CONVERTED_DIR" >&2
  exit 2
fi
if [ ! -d "$ORIGINAL_DIR" ]; then
  echo "Original litmus directory not found: $ORIGINAL_DIR" >&2
  exit 2
fi
if [ ! -f "$CALCULUS_BIN" ]; then
  echo "calculus_test.py not found at $CALCULUS_BIN" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 2
fi

shopt -s nullglob
files=("$CONVERTED_DIR"/*.litmus)
if [ ${#files[@]} -eq 0 ]; then
  echo "No .litmus files in $CONVERTED_DIR" >&2
  exit 0
fi

fail_count=0
echo "File | Expectation | Observed True? | Result"
echo "-----|-------------|----------------|-------"

for f in "${files[@]}"; do
  base="$(basename "$f")"
  orig="$ORIGINAL_DIR/$base"
  expect="UNKNOWN"
  if [ -f "$orig" ]; then
    # Extract the first OCaml-style block comment between '(*' and '*)' (if present)
    comments_block="$(awk 'BEGIN{in=0} /\(\*/{in=1; next} /\*\)/{in=0; next} in{print}' "$orig" 2>/dev/null | tr '\n' ' ' || true)"

    # Fallback: if no OCaml block, collect any comment text (/*...*/ or //...)
    if [ -z "$comments_block" ]; then
      comments_block="$(perl -0777 -ne 'while (/\/\/.*|\/\*.*?\*\/|\(\*.*?\*\)/gs) { print "$&\n" }' "$orig" 2>/dev/null || true)"
      comments_block="$(printf '%s' "$comments_block" | tr '\n' ' ')"
    fi

    # DEBUG: print extracted comment block for inspection (to stdout)
    printf 'DEBUG: comments_block for %s:\n----\n%s\n----\n' "$base" "$comments_block"

    # Use case-insensitive matching on the extracted comment text
    if [ -z "$comments_block" ]; then
      expect="UNKNOWN"
    else
      if printf '%s\n' "$comments_block" | grep -qiE 'result[^a-z0-9]*:.*never|\bnever\b'; then
        expect="Never"
      elif printf '%s\n' "$comments_block" | grep -qiE 'result[^a-z0-9]*:.*(may|possible|allowed|exists|always)|\b(may|possible|allowed|exists|always)\b'; then
        expect="May"
      else
        expect="UNKNOWN"
      fi
    fi
  else
    expect="NO_ORIGINAL"
  fi

  # run test and capture output
  outfile="$(mktemp)"
  if ! python3 "$CALCULUS_BIN" "$f" >"$outfile" 2>&1; then
    echo "$base | $expect | ERROR_RUNNING | FAIL"
    fail_count=$((fail_count+1))
    rm -f "$outfile"
    continue
  fi

  # determine whether output contains a standalone "True" token
  if grep -qE '\bTrue\b' "$outfile"; then
    observed="True"
  else
    observed="False"
  fi

  result="UNRESOLVED"
  case "$expect" in
    Never)
      if [ "$observed" = "True" ]; then
        result="PASS"
      else
        result="FAIL"
        fail_count=$((fail_count+1))
      fi
      ;;
    May)
      if [ "$observed" = "False" ]; then
        result="PASS"
      else
        result="WARN"  # observed True while comment suggested May/Allowed
      fi
      ;;
    NO_ORIGINAL)
      result="NO_BASELINE"
      ;;
    *)
      result="NO_EXPECTATION"
      ;;
  esac

  echo "$base | $expect | $observed | $result"

  rm -f "$outfile"
done

echo
if [ "$fail_count" -gt 0 ]; then
  echo "Completed with $fail_count failures." >&2
  exit 3
else
  echo "All tests matched expectations (or were non-failing)." 
  exit 0
fi
```# filepath: /home/kapoorh/Documents/calculus/run_and_compare_dat3m_lkmm.sh
#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run_and_compare_dat3m_lkmm.sh [converted_dir] [original_dir]
# Defaults assume repository layout:
#   converted: ./converted/Dat3M/LKMM
#   original:  ./litmus/Dat3M/LKMM

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERTED_DIR="${1:-$ROOT/converted/Dat3M/LKMM}"
ORIGINAL_DIR="${2:-$ROOT/litmus/Dat3M/LKMM}"
CALCULUS_BIN="$ROOT/calculus_test.py"

if [ ! -d "$CONVERTED_DIR" ]; then
  echo "Converted directory not found: $CONVERTED_DIR" >&2
  exit 2
fi
if [ ! -d "$ORIGINAL_DIR" ]; then
  echo "Original litmus directory not found: $ORIGINAL_DIR" >&2
  exit 2
fi
if [ ! -f "$CALCULUS_BIN" ]; then
  echo "calculus_test.py not found at $CALCULUS_BIN" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 2
fi

shopt -s nullglob
files=("$CONVERTED_DIR"/*.litmus)
if [ ${#files[@]} -eq 0 ]; then
  echo "No .litmus files in $CONVERTED_DIR" >&2
  exit 0
fi

fail_count=0
echo "File | Expectation | Observed True? | Result"
echo "-----|-------------|----------------|-------"

for f in "${files[@]}"; do
  base="$(basename "$f")"
  orig="$ORIGINAL_DIR/$base"
  expect="UNKNOWN"
  if [ -f "$orig" ]; then
    # Read original and find common expectation keywords in comments / top
    # Priority: Never -> expecting observed True (cycle/disallowed)
    # Next: Allowed/May/Always -> expecting observed False (no disallowing cycle)
    # Fallback: UNKNOWN
    txt="$(sed -n '1,40p' "$orig" | tr '\n' ' ')" # scan header area
    if echo "$txt" | grep -iq 'never'; then
      expect="Never"
    elif echo "$txt" | grep -Eiq 'always|allowed|may|possible|exists'; then
      expect="May"
    else
      # also check anywhere in file for a short comment like "Never"
      if grep -qi 'never' "$orig"; then
        expect="Never"
      elif grep -Eqi 'always|allowed|may|possible|exists' "$orig"; then
        expect="May"
      fi
    fi
  else
    expect="NO_ORIGINAL"
  fi

  # run test and capture output
  outfile="$(mktemp)"
  if ! python3 "$CALCULUS_BIN" "$f" >"$outfile" 2>&1; then
    echo "$base | $expect | ERROR_RUNNING | FAIL"
    fail_count=$((fail_count+1))
    rm -f "$outfile"
    continue
  fi

  # determine whether output contains a standalone "True" token
  if grep -qE '\bTrue\b' "$outfile"; then
    observed="True"
  else
    observed="False"
  fi

  result="UNRESOLVED"
  case "$expect" in
    Never)
      if [ "$observed" = "True" ]; then
        result="PASS"
      else
        result="FAIL"
        fail_count=$((fail_count+1))
      fi
      ;;
    May)
      if [ "$observed" = "False" ]; then
        result="PASS"
      else
        result="WARN"  # observed True while comment suggested May/Allowed
      fi
      ;;
    NO_ORIGINAL)
      result="NO_BASELINE"
      ;;
    *)
      result="NO_EXPECTATION"
      ;;
  esac

  echo "$base | $expect | $observed | $result"

  rm -f "$outfile"
done

echo
if [ "$fail_count" -gt 0 ]; then
  echo "Completed with $fail_count failures." >&2
  exit 3
else
  echo "All tests matched expectations (or were non-failing)." 
  exit 0
fi