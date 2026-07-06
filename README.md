# Calculus Artifact Branch

This branch is trimmed for artifact evaluation. The evaluator-facing workflow is:

1. Translate a Linux `.litmus` file or an RC11/C `.c` litmus test into the repository's calculus litmus format.
2. Run the translated test through `calculus_test.py` to obtain an `Allowed` or `Forbidden` result.

Use the single entry point:

```bash
python3 run_artifact_pipeline.py path/to/test.litmus
python3 run_artifact_pipeline.py path/to/test.c
```

Useful options:

```bash
python3 run_artifact_pipeline.py path/to/test.c --keep-translated /tmp/test.litmus
python3 run_artifact_pipeline.py converted/rc11/SB.litmus --kind converted
```

Supported inputs:

- Linux litmus tests handled by `scripts/linux_to_calculus.py`
- RC11/C litmus tests handled by `rc11_to_calculus.py`

Current RC11/C limitation:

- Atomic RMW/CAS operations such as `atomic_fetch_add_explicit(...)` are rejected with a clear error instead of producing malformed translated output.

Files and scripts kept on this branch are intended to support the translation-and-evaluation path above. Internal comparison scripts, reports, generated processing outputs, and TODO artifacts have been removed from this branch.
