# Task 4 Report: Expose Safe Tauri Engine Commands

## Status

Complete. The Tauri backend now exposes only the phase-one engine commands,
owns the engine through managed state, forwards engine notifications on the
specified event, starts the engine during setup, and blocks application exit
until graceful engine shutdown completes.

## Implementation

- Added Tauri commands `engine_status`, `engine_call`, and `engine_restart`.
- Restricted `engine_call` to `ping`, `rename.preview`, `rename.execute`, and
  `rename.undo`; all other methods return structured `METHOD_NOT_ALLOWED`.
- Applied a 300-second timeout to `rename.execute` and 120 seconds to all other
  allowed phase-one calls.
- Mapped `Starting` and `Ready` to frontend `starting` and `ready`; mapped
  `Stopped`, `Stopping`, and `Failed` to frontend `error` with a message.
- Added development discovery through repository `.venv/Scripts/python.exe`
  and `engine/server.py`; packaged discovery uses
  `resource_dir/engine/engine.exe` with no system-Python fallback.
- Generated a UUID v4 simple authentication token during Tauri setup and kept
  it only in Rust engine configuration and the Python child environment.
- Added `TauriEventSink` using event `engine-notification`; engine-originated
  notifications are forwarded and `engine.status` is emitted after startup,
  failure, and restart transitions.
- Registered `AppState` containing `Arc<EngineManager>` and all three commands.
- Added an asynchronous exit state machine that prevents exit requests until
  `EngineManager::shutdown()` completes, while deduplicating repeated requests.
- Preserved the Electron main, preload, shared, and renderer paths unchanged.
- Kept filesystem/path authorization out of this task for Task 5.

## TDD Evidence

Initial focused RED:

```text
cargo test --manifest-path electron-app/src-tauri/Cargo.toml commands::engine -- --nocapture
error[E0425]: cannot find function is_allowed_method
error[E0425]: cannot find function method_timeout
error[E0425]: cannot find function development_engine_config
```

Focused GREEN after implementation:

```text
running 3 tests
test result: ok. 3 passed; 0 failed
```

Additional hand-written status and packaged-discovery logic was returned to RED
by removing the implementation before adding tests; the focused build failed
on missing `map_engine_status` and `packaged_engine_config`, then passed 5/5
after the minimal implementation was restored.

The unexpected-exit event behavior was also test-first: the existing crash test
failed because no `engine.status` error notification was recorded, then passed
after the manager emitted the transition exactly when state changed to failed.

## Verification

Executed from the worktree root:

```powershell
cargo fmt --manifest-path electron-app/src-tauri/Cargo.toml -- --check
cargo clippy --manifest-path electron-app/src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path electron-app/src-tauri/Cargo.toml -- --nocapture
cargo build --manifest-path electron-app/src-tauri/Cargo.toml
```

Results: formatting clean, Clippy clean, 14 tests passed with zero failures, and
the Rust Tauri application build completed successfully.

## Self-Review

- Confirmed exact command names, allowlist values, timeout values, status/error
  mapping, event names, discovery paths, managed state, startup, and shutdown
  wiring against the Task 4 brief.
- Confirmed no arbitrary Python method is reachable and no auth token is
  serialized, emitted, or logged by the new command layer.
- Confirmed no path authorization was introduced and no Electron files changed.
- Added one narrow `clippy::too_many_arguments` allowance to the committed Task 3
  stdout-reader helper so the brief's required `-D warnings` command passes;
  this is annotation-only and does not alter manager behavior.

## Concerns

No blocking concerns. Renderer compatibility wiring and path authorization are
intentionally outside this task and remain for later tasks in the phase-one
plan.
