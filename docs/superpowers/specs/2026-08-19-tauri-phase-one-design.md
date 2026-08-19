# Tauri Phase One Migration Design

## Purpose

Migrate File Toolbox from an Electron-only desktop shell toward Tauri without
rewriting the existing Vue renderer or Python processing engine. Phase one
establishes a production-shaped Windows Tauri shell and validates the complete
rename workflow while the Electron application remains available as a working
fallback.

## Decisions

- The migration is Windows-only in phase one, matching the current product.
- Electron and Tauri coexist during the migration. Electron files, scripts,
  packaging, and behavior remain supported.
- Tauri lives at `electron-app/src-tauri/` and consumes the existing Vue
  renderer from `electron-app/renderer/`.
- The existing Python JSON-RPC engine remains the business-logic backend.
- Development uses the repository-root `.venv`; packaged builds use
  `engine/dist/engine.exe`.
- Phase one formally supports engine lifecycle, file selection and metadata,
  native drag-and-drop, and rename preview, execution, and undo.
- PDF split, scan split, custom preview protocol, auto-update, signing, and
  release installers are not considered migrated in phase one.

## Toolchain Baseline

- Windows target: `x86_64-pc-windows-msvc`
- Python: 3.14.6
- Rust: 1.97.1 stable MSVC
- Cargo: 1.97.1
- Node.js: 24.19.0
- npm: 11.17.0
- Tauri CLI: 2.11.4
- Frontend: Vue 3, TypeScript, and Vite

The project pins its Tauri JavaScript and Rust dependencies in source control.
The globally installed Tauri CLI is a developer convenience, not the only way
to reproduce the build.

## Repository Layout

```text
file-toolbox/
├── .venv/                         # ignored local Python environment
├── engine/                        # existing Python JSON-RPC entrypoint/build
├── src/                           # existing Python business logic
├── tests/                         # existing Python tests
└── electron-app/
    ├── main/                      # existing Electron main process
    ├── preload/                   # existing Electron bridge
    ├── renderer/                  # shared Vue renderer
    ├── shared/                    # shared desktop API types
    └── src-tauri/
        ├── Cargo.toml
        ├── tauri.conf.json
        └── src/
            ├── main.rs
            ├── engine/
            │   ├── mod.rs
            │   ├── error.rs
            │   ├── manager.rs
            │   └── protocol.rs
            └── commands/
                ├── mod.rs
                ├── engine.rs
                └── files.rs
```

Files are split by responsibility. `main.rs` only constructs application state,
registers commands, starts the engine, and coordinates shutdown. JSON parsing,
process ownership, and filesystem commands remain independently testable.

## Python Environment

Create `.venv` from Python 3.14.6 at the repository root and install the
existing `requirements.txt` without changing its dependency policy during this
phase. Run the complete Python test suite before modifying production code.

Development engine discovery is deterministic:

1. Resolve the repository root from Tauri development configuration.
2. Use `.venv/Scripts/python.exe` to launch `engine/server.py`.
3. Fail with a user-facing setup error if either path is missing.

Packaged engine discovery resolves the bundled `engine.exe` from Tauri's
resource directory. Phase one must not silently fall back to an arbitrary
system Python interpreter.

## Shared Renderer Bridge

Electron continues to expose `window.engine` and `window.electronAPI` through
its preload script. In Tauri, a small renderer bootstrap detects the Tauri
runtime and installs API objects with the same TypeScript interfaces.

The compatibility layer translates typed frontend calls into a small set of
Tauri commands:

```text
engine_status()
engine_call(method, params)
engine_restart()
open_files(options)
open_directory(options)
stat_paths(paths)
```

Tauri engine notifications are emitted to the webview as one event and are
adapted to the existing `EngineNotificationPayload` callback contract. Existing
Electron consumers remain unchanged. New Tauri-only branching stays inside the
platform bootstrap rather than spreading across Vue panels.

For APIs outside phase-one scope, the Tauri compatibility layer returns an
explicit `NOT_MIGRATED` error. The renderer exposes platform capabilities and
marks PDF, scan, update, and unsupported About actions unavailable in Tauri;
it must not present them as functioning features.

## Engine Process and JSON-RPC Flow

```text
Vue rename action
  -> Tauri TypeScript compatibility bridge
  -> invoke("engine_call", { method, params })
  -> Rust EngineManager
  -> one JSON-RPC request line on Python stdin
  -> Python response or notification line on stdout
  -> pending Rust request or Tauri event
  -> existing Vue result/notification handling
```

`EngineManager` is the sole owner of the Python child process. It maintains a
state machine (`stopped`, `starting`, `ready`, `failed`, `stopping`), a monotonic
request ID, serialized stdin access, and a map of pending response channels.

The stdout reader classifies each valid JSON line as one of:

- engine ready message;
- JSON-RPC response containing an `id`;
- JSON-RPC notification containing a `method`;
- malformed or unexpected output, which is logged without terminating the
  reader.

Only methods already exposed by the Electron application are accepted. Phase
one enables `ping`, `rename.preview`, `rename.execute`, and `rename.undo`.

Startup waits for the Python ready message and has a finite timeout. A child
exit or stdout closure rejects every pending request and moves the manager to a
failed state. Restart first completes shutdown of the old process and is
deduplicated so concurrent restart requests cannot create multiple engines.

Application shutdown sends the existing `shutdown` notification, waits for the
engine to flush history, then terminates it if the grace period expires. No
Python process may remain after the Tauri application exits.

## File Access and Drag-and-Drop

Tauri file and directory dialogs return native paths and register those paths
in backend authorization state. Rename requests validate source files and the
selected output directory against that state before being forwarded to Python.
The validation preserves the current distinction between individual file
authorization and recursive directory authorization.

File metadata is read by a Rust command with bounded input counts and structured
per-path errors. The renderer receives the same data shape it currently expects
from Electron.

HTML `File` objects do not expose reliable native paths in Tauri. Native Tauri
drag-and-drop events therefore feed paths into the renderer through the platform
bridge. Dropped paths are registered and validated by the backend before the
rename panel accepts them.

## Error Handling

Rust commands return serializable structured errors with a stable code and a
human-readable message. Internal process paths, authentication tokens, and Rust
backtraces are never sent to the renderer in production.

Required error categories are:

- `ENGINE_NOT_CONFIGURED`
- `ENGINE_START_FAILED`
- `ENGINE_NOT_READY`
- `ENGINE_TIMEOUT`
- `ENGINE_EXITED`
- `ENGINE_PROTOCOL_ERROR`
- `METHOD_NOT_ALLOWED`
- `PATH_NOT_AUTHORIZED`
- `PATH_NOT_FOUND`
- `NOT_MIGRATED`

The compatibility bridge converts these errors into the existing frontend error
format so current banners and dialogs can display them without platform-specific
logic.

## Testing Strategy

All behavior changes follow red-green-refactor.

Python baseline:

- create `.venv` and install `requirements.txt`;
- run the full existing test suite before migration work;
- keep the suite green throughout the migration.

Rust unit and integration tests cover:

- ready, response, notification, and malformed-line parsing;
- matching out-of-order responses to request IDs;
- request timeout and child exit behavior;
- deduplicated startup and restart;
- graceful shutdown followed by forced termination;
- allowlisted methods;
- file and directory authorization boundaries;
- structured file metadata errors.

TypeScript tests cover:

- Electron bridge preservation;
- Tauri runtime detection;
- exact method and parameter translation for rename calls;
- notification subscription cleanup;
- explicit unsupported-capability behavior.

Build verification includes:

- Python tests;
- renderer type checking and production build;
- Electron main/preload build;
- Rust formatting, linting, and tests;
- Tauri debug build;
- an end-to-end temporary-file smoke test for rename preview, copy, in-place
  rename, undo, engine restart, and application shutdown.

## Phase-One Acceptance Criteria

- The Electron application still builds and retains its existing behavior.
- The Tauri application starts the shared Vue renderer on Windows.
- Engine status, ping, restart, crash reporting, and shutdown work correctly.
- File dialog and native drag-and-drop paths can be added to Rename.
- Rename preview, copy, in-place rename, and undo work through Tauri.
- Unauthorized paths and missing files fail safely with understandable errors.
- Tauri shutdown leaves no Python child process.
- PDF, scan, update, preview-protocol, and packaging features are visibly marked
  unavailable rather than failing ambiguously.

## Out of Scope

- Rewriting Python business logic in Rust
- Removing Electron
- PDF split and scan split parity
- Tauri updater and GitHub release metadata
- Custom local-file preview protocol
- Code signing and production installers
- macOS, Linux, ARM64, and mobile targets
- Package-size optimization beyond recording the phase-one debug/release size

## Migration Continuation

After phase one passes, migrate PDF split and task notifications, then scan split
and preview resources, then auto-update and release packaging. Electron removal
is a separate decision made only after feature parity and release validation.
