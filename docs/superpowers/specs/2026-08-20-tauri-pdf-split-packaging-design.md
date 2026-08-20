# Tauri PDF Split and Packaging Design

## Purpose

Extend the experimental Windows Tauri application from rename-only support to
feature-complete ordinary PDF splitting, then produce a distributable NSIS
installer containing a phase-two Python engine. Preserve Electron as the full
feature fallback and leave scan splitting unchanged.

## Scope

Phase two includes:

- PDF validation and page-count inspection;
- single- and multi-file split preview;
- page-count, target-size, page-range, and bookmark split modes;
- asynchronous execution, queue state, progress, completion, failure, and
  cancellation;
- native file dialog and native Tauri file-drop input for PDF files;
- an independently packaged Python engine containing rename and ordinary PDF
  dependencies only;
- a Windows x64 NSIS installer and recorded size evidence.

Phase two explicitly excludes:

- changes to `src/core/pdf_scan_split_engine.py`;
- scan split commands, scan UI enablement, reference-image preview, or ROI
  behavior;
- OpenCV, NumPy, PyMuPDF, or ZXing in the phase-two Tauri engine;
- updater integration, code signing, MSI, portable, zip, macOS, Linux, ARM64,
  or removal of Electron.

## Decisions

- Reuse `src/core/pdf_split_engine.py` and the existing Vue
  `PdfSplitPanel.vue`; do not rewrite ordinary splitting in Rust in this phase.
- Extend the existing Tauri compatibility bridge instead of adding
  Tauri-specific branches throughout the panel.
- Keep the Rust command allowlist exact. Newly allowed methods are
  `pdf_split.validate`, `pdf_split.preview`, `pdf_split.preview_many`,
  `pdf_split.execute_async`, and `task.cancel`.
- Reuse the existing generic `engine-notification` event. No PDF-specific Tauri
  event channel is introduced.
- Preserve the Electron engine spec and Electron packaging configuration.
  Tauri receives a separate slim PyInstaller spec and output directory.
- Package only an NSIS installer in phase two. Use the default WebView2
  download bootstrapper so the installer does not embed the fixed WebView2
  runtime.

## Architecture

The data path remains the same shape as phase one:

```text
PdfSplitPanel.vue
  -> window.engine.pdfSplit / window.engine.cancelTask
  -> Tauri TypeScript compatibility bridge
  -> invoke("engine_call", { method, params })
  -> Rust allowlist and authorized-path validation
  -> EngineManager JSON-RPC request
  -> phase-two engine.exe (packaged) or engine/server.py (development)
  -> task.* JSON-RPC notifications
  -> Rust engine-notification event
  -> useEngineTask state and PdfSplitPanel UI
```

Electron continues to install the same `window.engine` contract through its
preload script. Shared API types do not fork by runtime.

## Rust Command Boundary

The Rust allowlist after phase two is exactly:

```text
ping
rename.preview
rename.execute
rename.undo
pdf_split.validate
pdf_split.preview
pdf_split.preview_many
pdf_split.execute_async
task.cancel
```

`task.cancel` receives a 10-second request timeout. Rename execution retains its
300-second timeout. PDF validation and preview use the existing 120-second
default. `pdf_split.execute_async` only reserves or starts a background task and
therefore also uses the 120-second request timeout; task duration is reported by
notifications rather than by holding the invoke request open.

The generic manager already forwards `task.progress`, `task.log`, `task.queued`,
and `task.complete`. Phase two must prove this path with renderer and real-engine
tests but must not add a second notification implementation.

## Path Authorization and Validation

Rust validates every PDF method before forwarding it to Python.

- `pdf_split.validate` and `pdf_split.preview` require one authorized regular
  file in `pdf_path`.
- `pdf_split.preview_many` and `pdf_split.execute_async` require a non-empty
  `pdf_paths` array of authorized regular files.
- Every source must have a case-insensitive `.pdf` extension on Windows and a
  size no greater than 200 MiB, matching Electron.
- `config` must be a JSON object when present.
- A non-empty `config.output_dir` must be an authorized existing directory.
- An empty output directory remains valid and preserves the current engine
  behavior of writing beside each source PDF.
- `task.cancel` contains no filesystem path and is not subject to path
  validation.
- Missing, unauthorized, wrong-type, oversized, or malformed paths return a
  stable structured desktop error before the engine receives the request.

The phase-one distinction remains intact: authorizing one file does not
authorize sibling inputs, while an explicitly selected directory authorizes
its descendants. Generated output paths returned by a successful PDF task are
not accepted as future inputs until they are selected or otherwise registered
through an authorized directory.

## Renderer Bridge and Capability Policy

The Tauri bridge translates PDF calls using the same parameter shapes as the
Electron preload:

```text
validate(pdfPath)
  -> pdf_split.validate { pdf_path }
preview(pdfPath, config)
  -> pdf_split.preview { pdf_path, config }
previewMany(pdfPaths, config)
  -> pdf_split.preview_many { pdf_paths, config }
executeAsync(pdfPaths, config, taskId)
  -> pdf_split.execute_async { pdf_paths, config, task_id }
cancelTask(taskId)
  -> task.cancel { task_id }
```

Rename behavior and unsupported scan behavior remain unchanged. The Tauri
capability object enables `rename` and `pdfSplit`, keeps `scanSplit` and
`update` disabled, and routes a previously stored `pdf_split` panel directly to
the working panel instead of sanitizing it back to rename.

`PdfSplitPanel.vue` subscribes to `electronAPI.onFileDrop` while mounted, filters
case-insensitive `.pdf` paths, reuses `appendFiles()`, and disposes the listener
on unmount. The existing HTML drop path remains for Electron.

## Phase-Two Python Engine

Add a separate PyInstaller spec for Tauri phase two. It includes:

- `engine/server.py`;
- `src.core.rename_engine`;
- `src.core.pdf_split_engine`;
- `src.utils.history_manager`;
- `src.utils.path_utils`;
- `src.utils.pdf_output`;
- `pypdf`.

It explicitly excludes:

- `src.core.pdf_scan_split_engine`;
- `cv2`;
- `numpy`;
- `fitz`;
- `zxingcpp`.

The output is `engine/dist-tauri/engine.exe`. Build work files use a separate
ignored directory so the existing `engine/dist/engine.exe` and Electron build
remain untouched. A post-build smoke test starts this executable, waits for the
ready notification, calls ping and ordinary PDF validation, verifies a scan
method is not exposed through Tauri, and shuts the process down cleanly.

## Tauri Resource and NSIS Packaging

`tauri.conf.json` maps the phase-two engine to the exact resource destination:

```json
{
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "resources": {
      "../../engine/dist-tauri/engine.exe": "engine/engine.exe"
    }
  }
}
```

The existing packaged engine discovery already resolves
`resource_dir/engine/engine.exe`; phase two keeps that contract. The package
script builds the phase-two engine first, then runs the Tauri release build. It
fails early with a clear message if the engine artifact is missing.

The installer uses per-user NSIS defaults and the WebView2 download
bootstrapper. Phase two records at least:

- release application executable size;
- `engine.exe` size;
- NSIS installer size;
- the existing Electron installer size when an artifact is available locally.

Size results are evidence, not a promise of the final scan-capable package.

## Error Handling

Existing desktop error codes remain stable. Phase two primarily uses:

- `METHOD_NOT_ALLOWED` for scan and unknown methods;
- `PATH_NOT_AUTHORIZED` for paths not selected through a trusted UI flow;
- `PATH_NOT_FOUND` for missing resources and files;
- `ENGINE_NOT_CONFIGURED` when packaged `engine.exe` is absent;
- `ENGINE_TIMEOUT`, `ENGINE_EXITED`, and `ENGINE_REQUEST_FAILED` for engine
  failures.

The renderer continues to use `formatEngineError()` and the existing task
completion UI. A cancellation may preserve already generated output files,
matching current Electron behavior and user messaging.

## Testing and Acceptance

All production behavior follows red-green-refactor.

Rust tests cover:

- the exact phase-two allowlist and timeout policy;
- method-specific PDF path extraction;
- authorized PDF acceptance;
- unauthorized, non-PDF, directory, oversized, malformed, and unauthorized
  output-directory rejection;
- `task.cancel` path-validation bypass;
- packaged engine resource discovery.

TypeScript tests cover:

- exact PDF and cancel parameter translation;
- PDF capability enablement with scan still disabled;
- native PDF drop subscription, filtering, deduplication, and cleanup;
- existing Electron globals remaining untouched.

Real-engine and GUI acceptance cover:

1. Add generated PDFs through the dialog and native drop.
2. Validate page counts.
3. Preview all four split modes.
4. Execute a multi-file task and observe progress and completion.
5. Cancel a sufficiently long task and verify the UI leaves the busy state.
6. Verify output files and source preservation.
7. Restart the engine after a task and confirm one child process.
8. Close the application and confirm no child engine remains.
9. Launch the installed NSIS build and repeat a short validate/preview/execute
   smoke flow.
10. Confirm scan navigation remains disabled with the migration explanation.

The full verification gate includes Python tests, renderer tests, TypeScript
checking, Electron production build, Rust formatting, Clippy with warnings as
errors, Rust tests, Tauri debug build, slim engine smoke tests, and the NSIS
release build.

## Implementation Coordination

Implementation tasks are split into disjoint file ownership where practical.
Subagents use `ikuuu/gpt-5.6-sol` first and switch to
`deepseek/deepseek-v4-flash` after the available ikuuu quota is exhausted. Each
subagent must use TDD, edit only its assigned file set, report changed paths and
test evidence, and stop before committing. The primary agent reviews every
patch, runs integration tests, resolves overlaps, and owns all commits and final
acceptance claims.

## Migration Continuation

After phase two, compare the measured installer size and operational behavior
before selecting the next step. Scan splitting remains on the Python/Electron
path until a separate design explicitly chooses a Rust/OpenCV, pure-Rust, or
continued-Python strategy.
