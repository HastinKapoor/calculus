# Calculus Artifact Branch

This branch keeps the artifact-facing translation and evaluation path only.

Workflow:

1. Start from a Linux `.litmus` file or a C `.litmus` litmus test.
2. Translate it into the repository's calculus litmus format.
3. Run `calculus.py` to obtain `Allowed` or `Forbidden`.

Use the single entry point:

```bash
python3 scripts/run_artifact_pipeline.py path/to/test.litmus
```

Useful options:

```bash
python3 scripts/run_artifact_pipeline.py path/to/test.litmus --keep-translated /tmp/test.translated.litmus
python3 scripts/run_artifact_pipeline.py converted/Kernel/MP+polocks.litmus --kind converted
```

Kept example inputs:

- Linux litmus examples under `litmus/Kernel/`
- C litmus examples under `litmus/c/`
- Paul McKenney RCU source litmus files under `litmus/paulmckrcu/`
- Example translated Linux litmus files under `converted/Kernel/`
- Paul McKenney RCU translated litmus files under `converted/paulmckrcu/`

Current C limitation:

- Atomic RMW/CAS operations such as `atomic_fetch_add_explicit(...)` are rejected with a clear error instead of producing malformed translated output.
