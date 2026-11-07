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

## Linux packaged video loading follow-up — 2026-11-07

- Python 3.14.9: 116 tests passed, 1 existing detector test skipped.
- Reproduced a system FFprobe failure with an incompatible `libz.so.1` on the frozen library path. The same tool succeeds with the isolated child environment; the parent environment stays unchanged.
- Source execution, the rebuilt PyInstaller executable, and `AppRun` extracted from the rebuilt AppImage all pass `--smoke-test-video` against a generated MP4. This checks UI metadata loading, OpenCV decoding, and FFmpeg image export.
- The AppImage's `--appimage-extract-and-run` invocation timed out on this host; explicit extraction and running its `AppRun` succeeded. CI verifies the actual extracted AppImage contents rather than relying on that runtime mode.
- The Windows packaged media check is configured in CI and was not run on this Linux host. Interactive playback remains a manual check.

## OpenCV/Qt crash follow-up — 2026-11-07

- The reported conversion abort loaded OpenCV's bundled Qt 5 alongside PyQt6. The native stack does not include the originating Qt fatal message, so it does not establish an exact crash cause.
- Requirements now select `opencv-python-headless`. Build preflight rejects GUI-enabled OpenCV, and bundle inspection rejects Qt 5 libraries.
- In a temporary environment using the actual headless wheel (OpenCV 5.0.0.93), 127 tests passed and 1 existing detector test skipped. Regression coverage includes three successive 30-to-24 FPS conversions through the UI and worker completion path.
- The rebuilt PyInstaller executable contains no Qt 5 libraries and passes packaged video loading, frame export, and asynchronous 30-to-24 FPS conversion under both offscreen Qt and X11/Xvfb.
- Linux packaging now performs the X11 media/conversion check on the extracted AppImage when Xvfb is available; CI installs it explicitly. Windows validation remains CI-only.
- The original user video and encoder settings were not available for reproduction. Existing AppImages must be rebuilt to receive the dependency change.

## Local AppImage build verification — 2026-11-07

- Built `OmniExtract-x86_64.AppImage` locally with headless OpenCV 5.0.0 and Qt 6.12.0. Package inspection found no Qt 5 libraries.
- The first extracted X11 check exposed a reproducible crash in `Union::Style::matches`, called by the bundled `UnionWidgetsStyle.so` during QApplication initialization. AppRun now selects built-in Fusion; three reruns against the previously failing extracted image passed with this setting.
- Repackaged the launcher change and verified the final image after extraction: offscreen and X11 30-to-24 FPS conversion passed, including a 720p H.264 source with AAC audio. The smoke test checks the output metadata is 24 FPS.
- A normal mounted AppImage launch under X11 also passed the 720p conversion. Set `APPIMAGELAUNCHER_DISABLE=1` for automated runs to avoid the desktop integration dialog; the build script scopes this to its own process.
- Test suite: 127 passed, 1 existing detector test skipped. Build-script syntax and diff checks passed. No commit was made.
- SHA-256: `d2231a1aacaac926f6193eef04023998f0aef2f731d37723845a1746340f3d64`. The adjacent `.sha256` file verifies successfully.
- The original user video still requires a manual check with this build.

## Confirmed Convert-button abort — 2026-11-07

- Journal entries for both reported ABRT processes (5902 and 38780) identify the same uncaught exception: `startFramerateConversion() takes 1 positional argument but 2 were given`, raised by `_guard_job` forwarding `clicked(bool)` into a no-argument action. This establishes the cause of those reported crashes; the earlier packaging corrections did not address that exception.
- The guard now consumes the optional checked boolean. Eighteen signal/slot regression cases exercise QPushButton clicks for all nine guarded actions, including checkable buttons. The real 30-to-24 FPS regression and packaged smoke check now click `conv_start_btn` rather than calling its handler directly.
- Journal entries also reported unavailable KIO file-protocol workers. Frozen Linux startup now selects Qt's own file dialogs before constructing QApplication.
- Tests: 145 passed, 1 existing detector test skipped.
- Rebuilt the executable and AppImage. The mounted final image passed three consecutive actual-button conversions of a 720p H.264/AAC source under X11, checking output metadata for 24 FPS. AppImageLauncher integration prompts were disabled only for these automated runs.
- Replaced the project-root `OmniExtract-x86_64.AppImage`; SHA-256: `1c06efe5c8706d4b9e78147a39a32a026668fe69c4b66d1157cf74446ef04931`. No commit was made.

## Push preparation — 2026-11-07

- Removed local build/dist/AppDir directories, generated AppImages and checksums, the downloaded packaging tool/spec file, and Python/pytest caches. The user moved the personal converted video out of the project.
- Final source validation with bytecode and pytest cache writes disabled: 151 passed, 1 existing detector test skipped. This includes success-only 100% progress updates.
- Shell syntax and diff whitespace checks pass. Workflow YAML parses; Linux Actions installs xvfb/xauth to run the packaged X11 conversion check, preserving the existing apt retry/timeout optimizations.
- The source is ready for manual commit and push to main. GitHub Actions will rebuild the artifacts; no new commit or push was made during cleanup.
