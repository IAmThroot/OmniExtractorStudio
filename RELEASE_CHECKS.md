# OmniExtract Studio 1.4.0 release checks

Checked on 2026-10-31 on Linux with Python 3.14.8.

- `python -m pytest -q`: 108 passed, 1 skipped. Bare `pytest` also works with the repository configuration.
- The existing detector test skips because it references an absent `dummy_model.onnx`; no real GPU/RIFE model execution is claimed.
- Regression coverage includes real FFmpeg slow/fast conform audio and frame counts, silent sources, segment export, final frame-write failures, slow RIFE inference, encoder failure, cancellation, child-process timeouts, metadata generations, settings compatibility, subtitle identity/cleanup, and batch restoration/continuation.
- Source startup and shutdown pass using `python extractor.py --smoke-test` with the offscreen Qt platform.
- A local PyInstaller Linux executable builds and passes the deterministic startup check.
- The Linux AppImage builds and passes `--appimage-extract-and-run --smoke-test` with exit status 0. Only application assets are packaged; the two user video files are excluded.
- Follow-up cleanup/dispatch checks cover locked temporary files and partial outputs, stream-close permission failures, preserved error details, exactly one outcome, and inherited completion formats across every worker type.
- The packaged startup checks above preceded this follow-up; rebuild the artifacts before publishing the latest source.
- Syntax compilation, build-script syntax validation, and `git diff --check` pass.

Windows tests, executable packaging, and startup checks run in the release workflow; they cannot be exercised on this Linux host. Real GPU/model inference and interactive playback remain manual checks.

CI rejects a release tag that differs from the package version. Canonical downloads retain the existing `OmniExtract-Windows.zip` and `OmniExtract-x86_64.AppImage` names and SHA-256 files.
