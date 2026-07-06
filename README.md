# Calculus Artifact Branch

This branch keeps the artifact-facing translation and evaluation path only.

Workflow:

1. Start from a Linux `.litmus` file or a C `.c` litmus test.
2. Translate it into the repository's calculus litmus format.
3. Run `calculus_test.py` to obtain `Allowed` or `Forbidden`.

Use the single entry point:

```bash
python3 scripts/run_artifact_pipeline.py path/to/test.litmus
python3 scripts/run_artifact_pipeline.py path/to/test.c
```

Useful options:

```bash
python3 scripts/run_artifact_pipeline.py path/to/test.c --keep-translated /tmp/test.litmus
python3 scripts/run_artifact_pipeline.py converted/c/SB.litmus --kind converted
```

Kept example inputs:

- Linux litmus examples under `litmus/Kernel/` and `litmus/RCU/`
- C litmus examples under `litmus/c/`
- Example translated Linux litmus files under `converted/Kernel/` and `converted/RCU/`
- Example translated C litmus files under `converted/c/`

Notes on the Linux corpus:

- `litmus/RCU/` contains the dedicated C-style RCU litmus tests.
- `litmus/Kernel/` also includes several Linux litmus tests with `RCU_...` names.

Kept scripts:

- `scripts/run_artifact_pipeline.py` for the full evaluator workflow
- `scripts/linux_to_calculus.py` for Linux litmus translation
- `scripts/c_to_calculus.py` for C litmus translation

Current C limitation:

- Atomic RMW/CAS operations such as `atomic_fetch_add_explicit(...)` are rejected with a clear error instead of producing malformed translated output.
