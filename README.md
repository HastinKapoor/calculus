# Calculus Artifact Branch

This branch keeps the artifact-facing translation, comparison, and evaluation path only.

Comparison workflow:

1. Start from a Linux `.litmus` file or a C `.litmus` litmus test.
2. Run it through `herd7`.
3. Translate it into the repository's calculus litmus format.
4. Run `calculus.py`.
5. Print whether the two tools match.

Use the comparison entry point:

```bash
python3 scripts/run_artifact_pipeline.py path/to/test.litmus
python3 scripts/run_artifact_pipeline.py --all
```

Translation + calculus-only workflow:

1. Start from a Linux `.litmus` file or a C `.litmus` litmus test.
2. Translate it into the repository's calculus litmus format.
3. Run `calculus.py` to obtain `Allowed` or `Forbidden`.

Use the calculus-only entry point:

```bash
python3 scripts/evaluate_calculus_pipeline.py path/to/test.litmus
```

Useful options:

```bash
python3 scripts/evaluate_calculus_pipeline.py path/to/test.litmus --keep-translated /tmp/test.translated.litmus
python3 scripts/evaluate_calculus_pipeline.py converted/Kernel/MP+polocks.litmus --kind converted
```

Kept example inputs:

- Linux litmus examples under `litmus/Kernel/`
- C litmus examples under `litmus/c/`
- Paul McKenney RCU source litmus files under `litmus/paulmckrcu/`
- Example translated Linux litmus files under `converted/Kernel/`
- Paul McKenney RCU translated litmus files under `converted/paulmckrcu/`

Current limitation:

- Atomic RMW/CAS operations such as `atomic_fetch_add_explicit(...)
- Address arithmetic
- Datarace detection
