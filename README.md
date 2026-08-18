# Calculus Artifact Branch

The artifact pipeline uses the following comparison workflow:

1. Start from a Linux `.litmus` file or a C `.litmus` litmus test.
2. Run it through `herd7`.
3. Translate it into the repository's calculus litmus format.
4. Run `calculus.py`.
5. Print whether the two tools match.

Use the comparison entry point:

```bash
python3 scripts/run_artifact_pipeline.py path/to/test.litmus --kind linux
python3 scripts/run_artifact_pipeline.py path/to/test.litmus --kind c
python3 scripts/run_artifact_pipeline.py --all
python3 scripts/run_artifact_pipeline.py --suite linux
python3 scripts/run_artifact_pipeline.py --suite c
python3 scripts/run_artifact_pipeline.py --suite RCU
python3 scripts/run_artifact_pipeline.py --suite interchange
```

--kind is used to specify which memory model to compare against in herd7. Using --kind c for a Linux program will fail to translate (e.g. READ_ONCE is not defined), using --kind linux for a C program will produce incorrect results (the LKMM applied to a C program is too weak).

For long interchange runs, you can stop early with `Ctrl+C` and still get a partial summary. You can also create `.interchange_stop` in the repo root to stop cleanly after the current source test finishes.

Translation + calculus-only workflow:

1. Start from a Linux `.litmus` file or a C `.litmus` litmus test.
2. Translate it into the repository's calculus litmus format.
3. Run `calculus.py` to obtain `Allowed` or `Forbidden`.

Use the calculus-only entry point:

```bash
python3 scripts/evaluate_calculus_pipeline.py path/to/test.litmus --kind [c/linux/converted]
```

Useful options:

```bash
python3 scripts/evaluate_calculus_pipeline.py path/to/test.litmus --keep-translated /tmp/test.translated.litmus
python3 scripts/evaluate_calculus_pipeline.py converted/Kernel/MP+polocks.litmus --kind converted
```

Memorder toggle consistency generates multiple variants of the given input, varying operation strength (e.g. write vs release) and language (c vs linux) to test interchangeability:

```bash
python3 scripts/check_memorder_toggle_consistency.py litmus
python3 scripts/check_memorder_toggle_consistency.py litmus/c
```

Example inputs:

- Linux litmus examples under `litmus/Kernel/`
- C litmus examples under `litmus/c/`
- Paul McKenney RCU source litmus files under `litmus/paulmckrcu/`
- Example translated Linux litmus files under `converted/Kernel/`
- Paul McKenney RCU translated litmus files under `converted/paulmckrcu/`

Current limitation:

- Address arithmetic
- Datarace detection
