# Tauri PDF Split and Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable ordinary PDF splitting in the Windows Tauri application and ship an NSIS installer containing a slim rename-and-PDF Python engine.

**Architecture:** Extend the existing exact Tauri command allowlist, method-specific Rust path validation, and TypeScript compatibility bridge; reuse the current Vue PDF panel and generic task notification flow. Package a separate PyInstaller engine that excludes every scan dependency, map it to `engine/engine.exe` as a Tauri resource, and leave Electron unchanged.

**Tech Stack:** Windows x64, Rust 1.97.1 MSVC, Tauri 2.11, Vue 3.5, TypeScript 5.7, Vite 8, Vitest 4, Python 3.14.6, PyInstaller 6, pypdf 5, NSIS

**Spec:** `docs/superpowers/specs/2026-08-20-tauri-pdf-split-packaging-design.md`

## Global Constraints

- Preserve Electron runtime behavior, preload contracts, `engine/engine.spec`, and `electron-app/electron-builder.yml`.
- Tauri phase two enables rename and ordinary PDF split only; scan split and update remain disabled.
- Newly allowed methods are exactly `pdf_split.validate`, `pdf_split.preview`, `pdf_split.preview_many`, `pdf_split.execute_async`, and `task.cancel`.
- PDF inputs must be authorized regular `.pdf` files no larger than 200 MiB.
- Non-empty output directories must be explicitly authorized existing directories; empty output directories remain valid.
- The Tauri engine must exclude `src.core.pdf_scan_split_engine`, `cv2`, `numpy`, `fitz`, and `zxingcpp`.
- The only release bundle target in this phase is Windows x64 NSIS with the default WebView2 download bootstrapper.
- Every hand-written behavior uses red-green-refactor. PyInstaller and Tauri configuration are configuration exceptions verified by metadata checks and real builds.
- Subagents edit only assigned files and do not commit. The primary agent reviews, verifies, and commits each task.

---

### Task 1: Enforce the Phase-Two Rust Command and Path Boundary

**Files:**
- Modify: `electron-app/src-tauri/src/commands/engine.rs`
- Modify: `electron-app/src-tauri/src/commands/files.rs`

**Interfaces:**
- Consumes: `PathAuthorizer`, `DesktopError`, existing `engine_call()`
- Produces: `validate_engine_params(method: &str, params: &Value, authorizer: &PathAuthorizer) -> Result<(), DesktopError>` and the exact phase-two method policy

- [ ] **Step 1: Write failing allowlist and timeout tests**

Replace the phase-one allowlist assertion in `commands/engine.rs` with explicit allowed and rejected sets:

```rust
#[test]
fn phase_two_allowlist_is_exact() {
    for method in [
        "ping",
        "rename.preview",
        "rename.execute",
        "rename.undo",
        "pdf_split.validate",
        "pdf_split.preview",
        "pdf_split.preview_many",
        "pdf_split.execute_async",
        "task.cancel",
    ] {
        assert!(is_allowed_method(method), "{method}");
    }
    for method in [
        "pdf_split.execute",
        "scan_split.execute_async",
        "scan_split.preview_reference",
        "history.get",
        "shutdown",
    ] {
        assert!(!is_allowed_method(method), "{method}");
    }
}

#[test]
fn task_cancel_uses_short_timeout() {
    assert_eq!(method_timeout("task.cancel"), Duration::from_secs(10));
    assert_eq!(method_timeout("pdf_split.execute_async"), Duration::from_secs(120));
    assert_eq!(method_timeout("rename.execute"), Duration::from_secs(300));
}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
cargo test commands::engine::tests::phase_two_allowlist_is_exact -- --nocapture
cargo test commands::engine::tests::task_cancel_uses_short_timeout -- --nocapture
```

Expected: the PDF methods are rejected and `task.cancel` returns 120 seconds.

- [ ] **Step 3: Implement the exact allowlist and timeout policy**

Update the production functions to this shape:

```rust
pub fn is_allowed_method(method: &str) -> bool {
    matches!(
        method,
        "ping"
            | "rename.preview"
            | "rename.execute"
            | "rename.undo"
            | "pdf_split.validate"
            | "pdf_split.preview"
            | "pdf_split.preview_many"
            | "pdf_split.execute_async"
            | "task.cancel"
    )
}

pub fn method_timeout(method: &str) -> Duration {
    match method {
        "task.cancel" => Duration::from_secs(10),
        "rename.execute" => Duration::from_secs(300),
        _ => Duration::from_secs(120),
    }
}
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
cargo test commands::engine::tests -- --nocapture
```

Expected: every engine command policy test passes.

- [ ] **Step 5: Write failing PDF path-validation tests**

In `commands/files.rs`, add tests that use the wished-for generic validator:

```rust
#[test]
fn pdf_validation_accepts_authorized_pdf_and_output_directory() {
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("source.pdf");
    let output = temp.path().join("out");
    std::fs::write(&source, b"%PDF-1.4\n").unwrap();
    std::fs::create_dir(&output).unwrap();
    let auth = PathAuthorizer::default();
    auth.authorize_file(&source).unwrap();
    auth.authorize_directory(&output).unwrap();

    assert!(validate_engine_params(
        "pdf_split.execute_async",
        &json!({"pdf_paths":[source],"config":{"output_dir":output}}),
        &auth,
    ).is_ok());
}

#[test]
fn pdf_validation_rejects_unauthorized_wrong_type_and_oversized_inputs() {
    let temp = tempfile::tempdir().unwrap();
    let unauthorized = temp.path().join("unauthorized.pdf");
    let wrong_type = temp.path().join("source.txt");
    let oversized = temp.path().join("large.pdf");
    std::fs::write(&unauthorized, b"%PDF").unwrap();
    std::fs::write(&wrong_type, b"%PDF").unwrap();
    std::fs::File::create(&oversized).unwrap()
        .set_len(MAX_INPUT_PDF_FILE_SIZE + 1).unwrap();
    let auth = PathAuthorizer::default();
    auth.authorize_file(&wrong_type).unwrap();
    auth.authorize_file(&oversized).unwrap();

    for params in [
        json!({"pdf_path":unauthorized}),
        json!({"pdf_path":wrong_type}),
        json!({"pdf_path":oversized}),
    ] {
        assert_eq!(
            validate_engine_params("pdf_split.validate", &params, &auth)
                .unwrap_err().code,
            "PATH_NOT_AUTHORIZED",
        );
    }
}

#[test]
fn pdf_validation_rejects_empty_lists_and_unauthorized_output_directory() {
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("source.pdf");
    let output = temp.path().join("out");
    std::fs::write(&source, b"%PDF").unwrap();
    std::fs::create_dir(&output).unwrap();
    let auth = PathAuthorizer::default();
    auth.authorize_file(&source).unwrap();

    assert_eq!(
        validate_engine_params(
            "pdf_split.preview_many",
            &json!({"pdf_paths":[],"config":{}}),
            &auth,
        ).unwrap_err().code,
        "PATH_NOT_AUTHORIZED",
    );
    assert_eq!(
        validate_engine_params(
            "pdf_split.execute_async",
            &json!({"pdf_paths":[source],"config":{"output_dir":output}}),
            &auth,
        ).unwrap_err().code,
        "PATH_NOT_AUTHORIZED",
    );
}

#[test]
fn cancel_and_non_path_methods_ignore_filesystem_validation() {
    let auth = PathAuthorizer::default();
    assert!(validate_engine_params("task.cancel", &json!({"task_id":"task-1"}), &auth).is_ok());
    assert!(validate_engine_params("ping", &json!({}), &auth).is_ok());
}

#[test]
fn pdf_validation_accepts_empty_output_directory_and_rejects_malformed_values() {
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("source.pdf");
    let directory_source = temp.path().join("directory.pdf");
    std::fs::write(&source, b"%PDF").unwrap();
    std::fs::create_dir(&directory_source).unwrap();
    let auth = PathAuthorizer::default();
    auth.authorize_file(&source).unwrap();
    auth.authorize_directory(&directory_source).unwrap();

    assert!(validate_engine_params(
        "pdf_split.preview",
        &json!({"pdf_path":source,"config":{"output_dir":""}}),
        &auth,
    ).is_ok());

    for params in [
        json!({"pdf_path":directory_source}),
        json!({"pdf_path":42}),
        json!({"pdf_path":source,"config":[]}),
        json!({"pdf_path":source,"config":{"output_dir":42}}),
    ] {
        assert_eq!(
            validate_engine_params("pdf_split.preview", &params, &auth)
                .unwrap_err().code,
            "PATH_NOT_AUTHORIZED",
        );
    }
}
```

- [ ] **Step 6: Run validation tests and verify RED**

Run:

```powershell
cargo test commands::files::tests::pdf_validation -- --nocapture
```

Expected: compilation fails because `MAX_INPUT_PDF_FILE_SIZE` and `validate_engine_params` do not exist.

- [ ] **Step 7: Implement method-specific validation**

Add:

```rust
pub const MAX_INPUT_PDF_FILE_SIZE: u64 = 200 * 1024 * 1024;

pub fn validate_engine_params(
    method: &str,
    params: &serde_json::Value,
    authorizer: &PathAuthorizer,
) -> Result<(), DesktopError> {
    validate_rename_params(method, params, authorizer)?;
    validate_pdf_split_params(method, params, authorizer)
}
```

Implement `validate_pdf_split_params()` with this routing:

```rust
let pdf_paths = match method {
    "pdf_split.validate" | "pdf_split.preview" => vec![required_string(params, "pdf_path")?],
    "pdf_split.preview_many" | "pdf_split.execute_async" => required_string_array(params, "pdf_paths")?,
    _ => return Ok(()),
};
```

For every path, require authorization, canonicalize it, require a regular file,
require a case-insensitive `pdf` extension, and reject metadata length greater
than `MAX_INPUT_PDF_FILE_SIZE`. If `config.output_dir` is a non-empty string,
call `validate_authorized_directory`; reject a non-object `config` and a
non-string `output_dir` with `PATH_NOT_AUTHORIZED`.

Change `engine_call()` to invoke `validate_engine_params()` instead of only
`validate_rename_params()`.

- [ ] **Step 8: Verify Task 1 and commit after primary review**

Run:

```powershell
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test commands::engine::tests -- --nocapture
cargo test commands::files::tests -- --nocapture
```

Expected: all focused Rust checks pass.

Primary commit:

```powershell
git add electron-app/src-tauri/src/commands/engine.rs electron-app/src-tauri/src/commands/files.rs
git commit -m "feat: authorize Tauri PDF split commands"
```

---

### Task 2: Translate PDF and Cancel Calls in the Tauri Bridge

**Files:**
- Modify: `electron-app/renderer/src/platform/tauri-bridge.test.ts`
- Modify: `electron-app/renderer/src/platform/tauri-bridge.ts`
- Modify: `electron-app/renderer/src/platform/runtime.test.ts`
- Modify: `electron-app/renderer/src/platform/runtime.ts`

**Interfaces:**
- Consumes: existing `EngineAPI`, `PdfSplitConfig`, and Rust `engine_call`
- Produces: exact PDF/cancel translations and `tauriPhaseTwoCapabilities`

- [ ] **Step 1: Write failing bridge translation tests**

Add a dedicated test:

```typescript
it("translates PDF split and cancellation calls exactly", async () => {
  const invoke = vi.fn().mockResolvedValue({});
  const bridge = createTauriBridge({ invoke, listen: vi.fn() });
  const config = { mode: "by_page_count" as const, page_count: 2, output_dir: "C:\\out" };

  await bridge.engine.pdfSplit.validate("C:\\a.pdf");
  await bridge.engine.pdfSplit.preview("C:\\a.pdf", config);
  await bridge.engine.pdfSplit.previewMany(["C:\\a.pdf"], config);
  await bridge.engine.pdfSplit.executeAsync(["C:\\a.pdf"], config, "pdf-task-1");
  await bridge.engine.cancelTask("pdf-task-1");

  expect(invoke.mock.calls).toEqual([
    ["engine_call", { method: "pdf_split.validate", params: { pdf_path: "C:\\a.pdf" } }],
    ["engine_call", { method: "pdf_split.preview", params: { pdf_path: "C:\\a.pdf", config } }],
    ["engine_call", { method: "pdf_split.preview_many", params: { pdf_paths: ["C:\\a.pdf"], config } }],
    ["engine_call", { method: "pdf_split.execute_async", params: { pdf_paths: ["C:\\a.pdf"], config, task_id: "pdf-task-1" } }],
    ["engine_call", { method: "task.cancel", params: { task_id: "pdf-task-1" } }],
  ]);
});
```

Remove PDF and `cancelTask` calls from the test named “rejects every unsupported
engine operation”; keep every scan method in that rejection list.

- [ ] **Step 2: Run the bridge test and verify RED**

Run:

```powershell
npm run test:renderer -- --run renderer/src/platform/tauri-bridge.test.ts
```

Expected: calls reject with `NOT_MIGRATED`.

- [ ] **Step 3: Implement exact bridge translations**

Import `PdfSplitConfig`, `PdfSplitPlan`, and `PdfSplitPreviewMany` as needed and
replace the PDF fallback block with:

```typescript
pdfSplit: {
  validate: (pdfPath) =>
    engineCall("pdf_split.validate", { pdf_path: pdfPath }),
  preview: (pdfPath, config) =>
    engineCall("pdf_split.preview", { pdf_path: pdfPath, config }),
  previewMany: (pdfPaths, config) =>
    engineCall("pdf_split.preview_many", { pdf_paths: pdfPaths, config }),
  executeAsync: (pdfPaths, config, taskId) =>
    engineCall("pdf_split.execute_async", {
      pdf_paths: pdfPaths,
      config,
      task_id: taskId,
    }),
},
cancelTask: (taskId) =>
  engineCall("task.cancel", { task_id: taskId }),
```

- [ ] **Step 4: Write failing capability tests**

Rename the imported capability and assert phase-two behavior:

```typescript
import { panelIsEnabled, sanitizePanel, tauriPhaseTwoCapabilities } from "./runtime";

it("enables rename and ordinary PDF split only", () => {
  expect(panelIsEnabled("rename", tauriPhaseTwoCapabilities)).toBe(true);
  expect(panelIsEnabled("pdf_split", tauriPhaseTwoCapabilities)).toBe(true);
  expect(panelIsEnabled("scan_split", tauriPhaseTwoCapabilities)).toBe(false);
  expect(panelIsEnabled("about", tauriPhaseTwoCapabilities)).toBe(true);
});

it("keeps an available saved PDF panel", () => {
  expect(sanitizePanel("pdf_split", tauriPhaseTwoCapabilities)).toBe("pdf_split");
  expect(sanitizePanel("scan_split", tauriPhaseTwoCapabilities)).toBe("rename");
});
```

- [ ] **Step 5: Run capability tests and verify RED**

Run:

```powershell
npm run test:renderer -- --run renderer/src/platform/runtime.test.ts
```

Expected: `tauriPhaseTwoCapabilities` is missing.

- [ ] **Step 6: Implement phase-two capabilities and installation**

Replace `tauriPhaseOneCapabilities` with:

```typescript
export const tauriPhaseTwoCapabilities: DesktopCapabilities = {
  rename: true,
  pdfSplit: true,
  scanSplit: false,
  update: false,
};
```

Update `tauri-bridge.ts` and its installation assertions to use this exported
object. Do not change `electronCapabilities`.

- [ ] **Step 7: Verify Task 2 and commit after primary review**

Run:

```powershell
npm run test:renderer -- --run renderer/src/platform/tauri-bridge.test.ts renderer/src/platform/runtime.test.ts
npm run typecheck
```

Primary commit:

```powershell
git add electron-app/renderer/src/platform
git commit -m "feat: bridge Tauri PDF split operations"
```

---

### Task 3: Accept Native PDF Drops in the Shared Panel

**Files:**
- Create: `electron-app/renderer/src/components/panels/PdfSplitPanel.test.ts`
- Modify: `electron-app/renderer/src/components/panels/PdfSplitPanel.vue`

**Interfaces:**
- Consumes: `electronAPI.onFileDrop`, existing `appendFiles()` and PDF validation
- Produces: mount-scoped native PDF drop subscription with cleanup

- [ ] **Step 1: Write the failing component test**

Create a happy-dom test that captures the native callback:

```typescript
// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import PdfSplitPanel from "./PdfSplitPanel.vue";

vi.mock("../../composables/useAppDialog", () => ({
  useAppDialog: () => ({ confirm: vi.fn().mockResolvedValue(true) }),
}));

describe("PdfSplitPanel native drops", () => {
  afterEach(() => vi.restoreAllMocks());

  it("adds unique PDF paths and disposes the native listener", async () => {
    let onDrop: ((paths: string[]) => void) | undefined;
    const dispose = vi.fn();
    Object.defineProperty(window, "electronAPI", {
      configurable: true,
      value: {
        onFileDrop: vi.fn((callback) => { onDrop = callback; return dispose; }),
        openFileDialog: vi.fn(),
        openDirectoryDialog: vi.fn(),
        getPathsForFiles: vi.fn().mockResolvedValue([]),
      },
    });
    const validate = vi.fn().mockResolvedValue({ valid: true, message: "OK", page_count: 1 });
    Object.defineProperty(window, "engine", {
      configurable: true,
      value: {
        pdfSplit: { validate, previewMany: vi.fn(), executeAsync: vi.fn() },
        cancelTask: vi.fn(),
        onNotification: vi.fn(() => () => {}),
      },
    });

    const wrapper = mount(PdfSplitPanel);
    onDrop?.(["C:\\a.pdf", "C:\\ignore.txt", "C:\\a.pdf"]);
    await flushPromises();

    expect(validate).toHaveBeenCalledTimes(1);
    expect(validate).toHaveBeenCalledWith("C:\\a.pdf");
    expect(wrapper.text()).toContain("a.pdf");
    wrapper.unmount();
    expect(dispose).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run the component test and verify RED**

Run:

```powershell
npm run test:renderer -- --run renderer/src/components/panels/PdfSplitPanel.test.ts
```

Expected: `validate` is never called because the panel has no native listener.

- [ ] **Step 3: Implement the mount-scoped subscription**

Import `onMounted`, add `let unsubscribeNativeDrop: (() => void) | null = null;`,
and register:

```typescript
onMounted(() => {
  unsubscribeNativeDrop = window.electronAPI?.onFileDrop?.((paths) => {
    const pdfPaths = Array.from(new Set(paths.filter((path) => /\.pdf$/i.test(path))));
    if (pdfPaths.length) void appendFiles(pdfPaths);
  }) ?? null;
});
```

Call `unsubscribeNativeDrop?.()` and clear the variable inside the existing
`onBeforeUnmount` block. Preserve the Electron HTML `onDrop()` path.

- [ ] **Step 4: Verify Task 3 and commit after primary review**

Run:

```powershell
npm run test:renderer -- --run renderer/src/components/panels/PdfSplitPanel.test.ts
npm run test:renderer
npm run typecheck
```

Primary commit:

```powershell
git add electron-app/renderer/src/components/panels/PdfSplitPanel.vue electron-app/renderer/src/components/panels/PdfSplitPanel.test.ts
git commit -m "feat: accept native PDF drops in Tauri"
```

---

### Task 4: Build and Smoke-Test the Slim Phase-Two Engine

**Files:**
- Modify: `.gitignore`
- Create: `engine/tauri_package_profile.py`
- Create: `engine/engine-tauri.spec`
- Create: `engine/smoke_tauri_engine.py`
- Create: `tests/test_tauri_package_profile.py`
- Modify: `electron-app/package.json`

**Interfaces:**
- Consumes: repository `.venv`, PyInstaller, `engine/server.py`
- Produces: `engine/dist-tauri/engine.exe`, `npm run tauri:engine`, and `npm run tauri:engine:smoke`

- [ ] **Step 1: Write the failing package-profile test**

Create:

```python
from engine.tauri_package_profile import EXCLUDES, HIDDEN_IMPORTS


def test_tauri_package_profile_keeps_pdf_and_excludes_scan_dependencies():
    assert {
        "src.core.rename_engine",
        "src.core.pdf_split_engine",
        "src.utils.history_manager",
        "src.utils.path_utils",
        "src.utils.pdf_output",
        "pypdf",
    } <= set(HIDDEN_IMPORTS)
    assert {
        "src.core.pdf_scan_split_engine",
        "cv2",
        "numpy",
        "fitz",
        "zxingcpp",
    } <= set(EXCLUDES)
    assert set(HIDDEN_IMPORTS).isdisjoint(EXCLUDES)
```

- [ ] **Step 2: Run the profile test and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tauri_package_profile.py -q
```

Expected: import fails because `engine.tauri_package_profile` does not exist.

- [ ] **Step 3: Implement the package profile and PyInstaller spec**

Create `engine/tauri_package_profile.py` with the exact lists from Step 1.

Create `engine/engine-tauri.spec` based on the existing spec, but import the
profile and use no scan collection helpers:

```python
from pathlib import Path
from tauri_package_profile import EXCLUDES, HIDDEN_IMPORTS

engine_dir = Path(SPECPATH).resolve()
project_root = str(engine_dir.parent)

a = Analysis(
    [str(engine_dir / "server.py")],
    pathex=[project_root, str(engine_dir)],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

- [ ] **Step 4: Verify the profile test GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tauri_package_profile.py -q
```

Expected: one test passes.

- [ ] **Step 5: Add build ignores and npm scripts**

Add to `.gitignore`:

```gitignore
engine/dist-tauri/
engine/build-tauri/
```

Add scripts to `electron-app/package.json`:

```json
{
  "tauri:engine": "..\\.venv\\Scripts\\python.exe -m PyInstaller ..\\engine\\engine-tauri.spec --clean --noconfirm --distpath ..\\engine\\dist-tauri --workpath ..\\engine\\build-tauri",
  "tauri:engine:smoke": "..\\.venv\\Scripts\\python.exe ..\\engine\\smoke_tauri_engine.py ..\\engine\\dist-tauri\\engine.exe"
}
```

- [ ] **Step 6: Implement the packaged-engine smoke script**

`smoke_tauri_engine.py` must:

1. resolve the executable from `sys.argv[1]` and fail if missing;
2. create a temporary one-page PDF with `pypdf.PdfWriter`;
3. set a random `FILE_TOOLBOX_ENGINE_TOKEN` and disable debug errors;
4. launch `engine.exe` with piped UTF-8 stdin/stdout and no shell;
5. wait at most 30 seconds for the `ready` notification;
6. call `ping` and require `{"pong": true}`;
7. call `pdf_split.validate` and require `valid: true` and `page_count: 1`;
8. call `shutdown`, require its response, close stdin, and require exit code 0
   within 10 seconds;
9. terminate the child in `finally` only when it is still running.

Use monotonically increasing request IDs and include the auth token on every
request. Print one final JSON summary containing `ready`, `pong`,
`pdf_valid`, and `page_count` for machine-readable evidence.

The packaged-engine smoke test exercises only the engine transport. The Rust
allowlist test from Task 1 is the required proof that scan methods are not
exposed through Tauri.

- [ ] **Step 7: Build and smoke-test the slim engine**

Run from `electron-app`:

```powershell
npm run tauri:engine
npm run tauri:engine:smoke
```

Expected: PyInstaller creates `engine/dist-tauri/engine.exe`; the smoke summary
reports `ready`, `pong`, and `pdf_valid` true with one page.

- [ ] **Step 8: Prove excluded modules are not collected**

Run:

```powershell
rg -n "src\.core\.pdf_scan_split_engine|cv2|numpy|fitz|zxingcpp" ..\engine\build-tauri\engine\Analysis-00.toc
```

Expected: no collected module entry. If PyInstaller records excluded names as
metadata, inspect the matching lines and verify they are under the excludes
section rather than binaries, pure modules, or hidden imports.

- [ ] **Step 9: Verify Task 4 and commit after primary review**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tauri_package_profile.py -q
Set-Location electron-app
npm run tauri:engine:smoke
```

Primary commit:

```powershell
git add .gitignore engine/tauri_package_profile.py engine/engine-tauri.spec engine/smoke_tauri_engine.py tests/test_tauri_package_profile.py electron-app/package.json
git commit -m "build: package slim Tauri Python engine"
```

---

### Task 5: Bundle the Engine in a Windows NSIS Installer

**Files:**
- Modify: `electron-app/src-tauri/tauri.conf.json`
- Modify: `electron-app/package.json`
- Test: `electron-app/node_modules/@tauri-apps/cli/config.schema.json` through Tauri CLI validation

**Interfaces:**
- Consumes: `engine/dist-tauri/engine.exe`, existing `packaged_engine_config()`
- Produces: `npm run tauri:package:nsis` and an installer under `src-tauri/target/release/bundle/nsis/`

- [ ] **Step 1: Update bundle configuration**

Set:

```json
{
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "resources": {
      "../../engine/dist-tauri/engine.exe": "engine/engine.exe"
    },
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.ico"
    ],
    "windows": {
      "webviewInstallMode": { "type": "downloadBootstrapper" }
    }
  }
}
```

Keep existing product name, version, identifier, window settings, CSP, and
Android debug suffix unchanged.

- [ ] **Step 2: Add the release packaging script**

Add:

```json
{
  "tauri:package:nsis": "npm run tauri:engine && npm run tauri:engine:smoke && tauri build --bundles nsis"
}
```

- [ ] **Step 3: Validate configuration before the expensive build**

Run:

```powershell
npm run tauri -- info
npm run tauri:build:debug
```

Expected: Tauri accepts the configuration and the debug no-bundle application
still builds. The existing development engine remains `.venv/Scripts/python.exe
engine/server.py`.

- [ ] **Step 4: Build the NSIS installer**

Run:

```powershell
npm run tauri:package:nsis
```

Expected: release compilation succeeds and exactly one phase-two NSIS installer
is created below `electron-app/src-tauri/target/release/bundle/nsis/`.

- [ ] **Step 5: Inspect packaged resource placement**

Verify the release resource tree or installed test copy contains
`engine/engine.exe`, and run:

```powershell
Get-ChildItem -Recurse src-tauri\target\release\bundle\nsis | Select-Object FullName,Length
```

Record the installer path and length for Task 6.

- [ ] **Step 6: Commit configuration after primary review**

```powershell
git add electron-app/src-tauri/tauri.conf.json electron-app/package.json
git commit -m "build: bundle Tauri PDF split installer"
```

---

### Task 6: Verify Real PDF Workflows and Record Phase-Two Evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-20-tauri-pdf-split-packaging.md` only to check completed steps

**Interfaces:**
- Consumes: completed phase-two branch, generated PDFs, debug app, installed NSIS app
- Produces: reproducible commands, acceptance evidence, and measured sizes

- [ ] **Step 1: Run the full automated verification gate**

Run from the worktree root and `electron-app` as appropriate:

```powershell
.venv\Scripts\python.exe -m pytest
npm run test:renderer
npm run typecheck
npm run build
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
npm run tauri:engine
npm run tauri:engine:smoke
npm run tauri:build:debug
npm run tauri:package:nsis
```

Expected: every command exits 0. Do not claim phase completion from partial
results.

- [ ] **Step 2: Generate deterministic GUI fixtures**

Use `.venv/Scripts/python.exe` and `pypdf.PdfWriter` to create, in a temporary
directory outside the repository:

- `pages-6.pdf`: six blank pages with different dimensions;
- `ranges-8.pdf`: eight blank pages;
- `bookmarks-4.pdf`: four pages with two top-level outline entries;
- `cancel-large.pdf`: enough repeated pages to keep execution cancellable on
  the test machine;
- an empty output directory.

Do not commit generated PDFs.

- [ ] **Step 3: Exercise the Tauri debug application**

Run `npm run tauri:dev` and verify:

1. Rename and ordinary PDF navigation are enabled; scan remains disabled.
2. Dialog selection validates page counts.
3. Native drop adds a PDF exactly once and ignores a non-PDF file.
4. Preview succeeds for page-count, target-size, page-range, and bookmark modes.
5. Multi-file execution emits progress and completes with expected outputs.
6. Cancellation leaves the UI non-busy and reports preserved partial outputs
   when present.
7. An unselected path sent by a test harness is rejected with
   `PATH_NOT_AUTHORIZED`.
8. Engine restart leaves one child process.
9. Closing Tauri leaves no child whose command line contains this worktree or
   `engine/server.py`.

- [ ] **Step 4: Install and smoke-test the NSIS build**

Install the generated per-user NSIS package, launch it, add `pages-6.pdf`,
preview a two-page split, execute it to the selected output directory, verify
three output PDFs, close the application, and confirm no `engine.exe` child
remains. Uninstall through Windows after recording evidence.

- [ ] **Step 5: Record exact size evidence**

Collect:

```powershell
Get-Item engine\dist-tauri\engine.exe | Select-Object FullName,Length
Get-Item electron-app\src-tauri\target\release\app.exe | Select-Object FullName,Length
Get-ChildItem electron-app\src-tauri\target\release\bundle\nsis -Filter '*.exe' | Select-Object FullName,Length
Get-ChildItem electron-app\release -Filter '*setup*.exe' -ErrorAction SilentlyContinue | Select-Object FullName,Length
```

Report bytes and MiB. Label the Electron comparison unavailable when no local
artifact exists; do not rebuild Electron installers merely to fill the table.

- [ ] **Step 6: Update README**

Change the experimental Tauri section to “第二阶段（Windows、重命名 + 普通
PDF 拆分）”. Document:

```powershell
Set-Location electron-app
npm ci
npm run tauri:dev
npm run tauri:engine
npm run tauri:package:nsis
```

State that scan split, updater, signing, and non-Windows targets remain out of
scope. Add the exact measured engine, release app, and NSIS installer sizes.

- [ ] **Step 7: Final verification and documentation commit**

Run:

```powershell
git diff --check
git status --short
```

Review every acceptance item against fresh evidence, then commit:

```powershell
git add README.md docs/superpowers/plans/2026-08-20-tauri-pdf-split-packaging.md
git commit -m "docs: record Tauri PDF split verification"
```

Expected: clean `codex/tauri-migration` worktree with scan code unchanged.
