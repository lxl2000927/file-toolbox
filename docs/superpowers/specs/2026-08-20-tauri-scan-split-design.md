# Tauri Scan Split Migration Design

## Purpose

Migrate the existing PDF scan-split workflow into the Windows Tauri runtime
without changing the scan-split core algorithm or its Python task behavior.
The migration will reuse the existing Python JSON-RPC routes and Vue panel,
adding only the Tauri command boundary, capability enablement, packaging
dependencies, and verification coverage required to run the same workflow in
the Tauri application.

## Scope

The Windows Tauri application will support the existing scan-split workflow:

- QR-code, stamp, feature-image, and automatic detection modes;
- image or PDF reference files;
- reference preview and ROI selection;
- single-page probe and quick scan;
- asynchronous scan execution, queue state, progress, logs, cancellation,
  and preservation of already generated outputs;
- authorized PDF input, authorized reference files, and authorized output
  directories;
- Windows x64 development, release, and NSIS packaging.

Non-Windows targets, updater support, code signing, and changes to the scan
algorithm remain out of scope.

## Non-Negotiable Constraints

- Do not modify `src/core/pdf_scan_split_engine.py`.
- Do not change the scan-specific execution logic in `engine/server.py` other
  than narrowly required protocol or packaging compatibility fixes.
- Scan PDF inputs must be authorized through the Tauri `PathAuthorizer` before
  reaching the Python engine.
- Ordinary PDF split and scan PDF inputs use the same 1 GiB boundary. The
  boundary is a shared Rust constant and can be changed independently if
  operational evidence requires a different value.
- Reference images remain limited to 15 MiB; reference PDFs use the same 1 GiB
  PDF boundary.
- An output directory, when supplied, must be an explicitly authorized
  existing directory. An empty output directory remains valid and preserves
  the current Python default-output behavior.
- The existing Electron path and behavior must remain unchanged except that
  its PDF input boundary is raised from 200 MiB to the same 1 GiB boundary.

## Architecture

The Tauri path will mirror the already implemented Electron path:

```text
ScanSplitPanel.vue
  -> window.engine.scanSplit
  -> tauri-bridge.ts
  -> invoke("engine_call", { method, params })
  -> Rust method allowlist and authorized-path validation
  -> EngineManager JSON-RPC request
  -> development server.py or packaged engine.exe
  -> task.progress / task.log / task.complete notifications
  -> engine-notification event
  -> useEngineTask and ScanSplitPanel.vue
```

No second scan process and no Rust implementation of QR, stamp, feature, or
ROI detection will be introduced.

## Tauri Engine API

The following methods become allowed in the Rust boundary:

- `scan_split.preview_reference`
- `scan_split.probe_page`
- `scan_split.scan_only`
- `scan_split.execute_async`
- existing `task.cancel`

The TypeScript bridge will preserve the existing `EngineAPI.scanSplit`
signatures and translate camelCase arguments to the Python snake_case
protocol fields exactly as Electron preload already does.

The asynchronous methods continue to return a task identifier immediately.
The Python engine remains the source of truth for task type, progress, log,
completion, cancellation, result, and error payloads.

## Path Authorization

Rust validation will be method-specific:

| Method | Required paths | Optional paths |
| --- | --- | --- |
| `preview_reference` | authorized reference image or PDF | none |
| `probe_page` | authorized PDF | authorized reference image or PDF |
| `scan_only` | authorized PDF | authorized reference image or PDF |
| `execute_async` | authorized PDF | authorized reference image or PDF; authorized output directory |

An empty reference path is allowed for detection modes that do not require a
reference. A non-empty reference path is always canonicalized and checked for
its supported extension and size before the engine call. Validation rejects
unselected paths, directories supplied as files, unsupported extensions,
missing files, oversized inputs, and unauthorized output directories with the
existing `PATH_NOT_AUTHORIZED` family of errors.

The Rust layer validates filesystem identity, type, and size only. Detection
parameters continue to be normalized by the existing Python
`PdfScanSplitOptions` implementation.

## Renderer Capability

Replace the phase-two capability state with a phase-three state that enables
`scanSplit` while keeping `update` disabled. The existing panel navigation and
session-panel sanitization should then expose the already implemented scan
panel without duplicating UI logic.

The first scan migration does not add a new native-drop interaction to the
scan panel. The panel already uses the native file dialog and the Tauri dialog
path is authorized. Native drop routing can be added later as an isolated UX
improvement without changing the scan protocol.

History APIs remain a separate follow-up. The scan engine may continue to
write history records, but enabling scan execution does not implicitly expand
the phase to migrate the Tauri history viewer.

## Packaged Python Engine

The Tauri PyInstaller profile will be expanded from the current rename/PDF
profile to include the existing scan dependencies:

- `src.core.pdf_scan_split_engine`;
- `cv2` and its collected submodules and dynamic libraries;
- `numpy`;
- `fitz` / PyMuPDF;
- `zxingcpp` and its collected submodules and dynamic libraries.

The engine executable continues to be bundled at the same Tauri resource path
so the Rust packaged-engine discovery code does not change. The resulting
engine and NSIS installer will be larger than the phase-two baseline; exact
sizes will be measured after the scan-inclusive build rather than treated as
a fixed target.

## Testing Strategy

Automated coverage will include:

- Rust allowlist, timeout, authorized-path, extension, size, and empty-path
  tests for all scan methods;
- TypeScript bridge tests for all four scan calls, argument translation, and
  capability enablement;
- Python package-profile tests asserting that scan modules and native runtime
  dependencies are collected;
- packaged-engine smoke tests that load the scanner and exercise reference
  preview, single-page probe, quick scan, and a real scan execution;
- existing Electron and Python scan tests unchanged and passing.

Windows GUI acceptance will use deterministic fixtures and verify:

1. the scan panel is enabled in Tauri;
2. dialog-selected PDFs and references work;
3. reference preview and ROI selection work;
4. each detection mode reaches the existing engine;
5. probe, quick scan, and full execution show progress and results;
6. cancellation leaves the UI idle and preserves partial outputs;
7. an unselected PDF or reference path is rejected before engine execution;
8. the scan-inclusive NSIS installer starts the same workflow and shuts down
   without leaving an engine child process.

The existing NSIS install/run/uninstall acceptance is considered complete for
the current package. The scan-inclusive package still needs one focused
installed-app scan smoke test because its embedded Python engine changes.

## Migration Completion Criteria

The scan migration is complete when the Tauri Windows x64 workflow passes the
automated gate, the installed scan-inclusive NSIS build passes the focused
scan smoke test, the existing scan core files are unchanged, the capability
state exposes scan split, and the documented scope explicitly excludes
non-Windows, updater, and signing work.
