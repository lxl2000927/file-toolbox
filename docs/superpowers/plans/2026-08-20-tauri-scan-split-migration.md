# Tauri Scan Split Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Enable the existing PDF scan-split workflow in the Windows Tauri application while leaving the Python scan algorithm unchanged and raising the PDF input limit to 1 GiB in both Electron and Tauri.

**Architecture:** Reuse the existing Python JSON-RPC scan routes and Vue ScanSplitPanel. Add Tauri method allowlisting and authorized-path validation, implement the existing TypeScript bridge methods, enable the scan capability, and package the existing scanner dependencies into the Tauri Python engine.

**Tech Stack:** Rust/Tauri 2, Tokio, TypeScript, Vue 3, Vitest, Python 3.14, PyMuPDF, OpenCV, NumPy, ZXing-C++, PyInstaller, pytest, Windows x64 NSIS.

**Spec:** docs/superpowers/specs/2026-08-20-tauri-scan-split-design.md

## Global Constraints

- Do not modify src/core/pdf_scan_split_engine.py.
- Do not change scan-specific execution logic in engine/server.py.
- Both ordinary PDF split and scan split use 1 * 1024 * 1024 * 1024 bytes as the PDF input limit.
- Scan PDFs, ordinary PDFs, and PDF references must be authorized paths before reaching the Python engine.
- Reference images remain limited to 15 * 1024 * 1024 bytes.
- Non-empty output directories must be authorized existing directories; empty output directories remain valid.
- Keep updater, code signing, and non-Windows targets out of scope.
- Preserve Electron behavior except for the PDF size-limit increase in electron-app/main/index.ts.
- Use red-green-refactor for handwritten logic and run focused tests after each implementation step.

---

### Task 1: Unify the 1 GiB PDF Boundary and Add Tauri Scan Path Validation

**Files:**
- Modify: electron-app/main/index.ts:62-64
- Modify: electron-app/src-tauri/src/commands/engine.rs:26-46
- Modify: electron-app/src-tauri/src/commands/files.rs:10-226
- Test: Rust tests in electron-app/src-tauri/src/commands/engine.rs
- Test: Rust tests in electron-app/src-tauri/src/commands/files.rs

**Interfaces:**
- Consumes: PathAuthorizer, DesktopError, engine_call(), and Electron validateInputFile().
- Produces: a shared 1 GiB PDF boundary and method-specific validation for all scan_split calls.

- [ ] **Step 1: Write failing allowlist and limit tests**

Extend the Rust allowlist test with the exact scan methods and timeouts:

~~~rust
for method in [
    "scan_split.preview_reference",
    "scan_split.probe_page",
    "scan_split.scan_only",
    "scan_split.execute_async",
] {
    assert!(is_allowed_method(method), "{method}");
}

assert_eq!(method_timeout("scan_split.execute_async"), Duration::from_secs(120));
assert_eq!(method_timeout("scan_split.preview_reference"), Duration::from_secs(120));
~~~

Add a sparse-file boundary test in files.rs. It must set the file length rather
than writing 1 GiB of content:

~~~rust
#[test]
fn pdf_validation_accepts_files_at_one_gib_and_rejects_larger_files() {
    let temp = tempfile::tempdir().unwrap();
    let accepted = temp.path().join("accepted.pdf");
    let rejected = temp.path().join("rejected.pdf");
    std::fs::File::create(&accepted)
        .unwrap()
        .set_len(MAX_INPUT_PDF_FILE_SIZE)
        .unwrap();
    std::fs::File::create(&rejected)
        .unwrap()
        .set_len(MAX_INPUT_PDF_FILE_SIZE + 1)
        .unwrap();

    let auth = PathAuthorizer::default();
    auth.authorize_file(&accepted).unwrap();
    auth.authorize_file(&rejected).unwrap();

    assert!(validate_engine_params(
        "pdf_split.validate",
        &serde_json::json!({"pdf_path": accepted}),
        &auth,
    ).is_ok());
    assert_eq!(
        validate_engine_params(
            "pdf_split.validate",
            &serde_json::json!({"pdf_path": rejected}),
            &auth,
        ).unwrap_err().code,
        "PATH_NOT_AUTHORIZED",
    );
}
~~~

Add scan validation tests for every scan method: authorized PDF, unauthorized
PDF, wrong extension, authorized image reference, authorized PDF reference,
oversized reference image, and unauthorized output directory. Also assert that
an empty reference path is accepted for scan_only and execute_async.

Run from electron-app/src-tauri:

~~~powershell
cargo test commands::engine::tests::phase_two_allowlist_is_exact -- --nocapture
cargo test commands::files::tests::pdf_validation -- --nocapture
~~~

Expected: the new tests fail because scan methods are not allowed and scan
validation does not exist.

- [ ] **Step 2: Implement the shared PDF size constants**

Change the Electron limit in electron-app/main/index.ts to:

~~~ts
const MAX_INPUT_PDF_FILE_SIZE = 1 * 1024 * 1024 * 1024;
~~~

Keep MAX_GENERIC_INPUT_FILE_SIZE at 500 MiB and
MAX_REFERENCE_IMAGE_FILE_SIZE at 15 MiB.

In electron-app/src-tauri/src/commands/files.rs use:

~~~rust
pub const MAX_INPUT_PDF_FILE_SIZE: u64 = 1 * 1024 * 1024 * 1024;
pub const MAX_REFERENCE_IMAGE_FILE_SIZE: u64 = 15 * 1024 * 1024;
~~~

Run npm run typecheck. Expected: PASS.

- [ ] **Step 3: Add the scan method allowlist**

Extend is_allowed_method() in commands/engine.rs with
scan_split.preview_reference, scan_split.probe_page, scan_split.scan_only,
and scan_split.execute_async. Keep task.cancel at 10 seconds and the default
engine request timeout at 120 seconds. Keep history, shutdown, and unrelated
methods rejected.

Run:

~~~powershell
cargo test commands::engine::tests -- --nocapture
~~~

Expected: PASS, including the exact allowed/rejected method assertions.

- [ ] **Step 4: Implement method-specific scan path validation**

Add validate_scan_split_params() and call it from validate_engine_params().
Use these rules:

~~~rust
match method {
    "scan_split.preview_reference" => {
        let reference = required_string(params, "reference_image_path")?;
        validate_scan_reference(Path::new(&reference), authorizer)?;
    }
    "scan_split.probe_page" | "scan_split.scan_only" => {
        validate_scan_pdf_params(params, authorizer)?;
    }
    "scan_split.execute_async" => {
        validate_scan_pdf_params(params, authorizer)?;
        if let Some(output_dir) = params.get("output_dir") {
            let output_dir = output_dir
                .as_str()
                .ok_or_else(|| DesktopError::new("PATH_NOT_AUTHORIZED", "输出目录格式无效"))?;
            if !output_dir.trim().is_empty() {
                validate_authorized_directory(Path::new(output_dir), authorizer)?;
            }
        }
    }
    _ => return Ok(()),
}
~~~

validate_scan_pdf_params() must require an authorized .pdf pdf_path and
validate reference_image_path only when it is a non-empty string.
validate_scan_reference() accepts png, jpg, jpeg, bmp, tiff, tif, webp, or gif
up to 15 MiB, and PDF references up to 1 GiB. Reuse canonicalization and the
existing path-error helpers.

Run:

~~~powershell
cargo test commands::files::tests -- --nocapture
~~~

Expected: PASS, including unauthorized, wrong-type, oversized, and empty
reference cases.

- [ ] **Step 5: Verify core files are untouched and commit**

Run from the worktree root:

~~~powershell
git diff --check
git diff -- src/core/pdf_scan_split_engine.py engine/server.py
~~~

Expected: no diff for either scan core file. Commit:

~~~powershell
git add electron-app/main/index.ts electron-app/src-tauri/src/commands/engine.rs electron-app/src-tauri/src/commands/files.rs
git commit -m "feat: authorize Tauri scan split paths"
~~~

---

### Task 2: Implement the Tauri Scan Bridge and Enable the Panel

**Files:**
- Modify: electron-app/renderer/src/platform/tauri-bridge.ts:120-125,182-195
- Modify: electron-app/renderer/src/platform/runtime.ts:17-22
- Modify: electron-app/renderer/src/platform/tauri-bridge.test.ts:1-205
- Modify: electron-app/renderer/src/platform/runtime.test.ts:1-17

**Interfaces:**
- Consumes: EngineAPI.scanSplit, engineCall(), Tauri invoke, and engine-notification.
- Produces: real Tauri implementations for previewReference, probePage, scanOnly, and executeAsync, plus enabled scan navigation.

- [ ] **Step 1: Write failing bridge translation tests**

Replace the current NOT_MIGRATED scan test with exact call assertions:

~~~ts
it("translates every scan split call exactly", async () => {
  const invoke = vi.fn().mockResolvedValue({ task_id: "scan-task" });
  const bridge = createTauriBridge({ invoke, listen: vi.fn() });
  const options = { detection_mode: "qrcode" as const, dpi: 220 };

  await bridge.engine.scanSplit.previewReference("C:\\ref.png", {
    nfeatures: 1200,
    roi: [1, 2, 300, 400],
  });
  await bridge.engine.scanSplit.probePage({
    pdfPath: "C:\\input.pdf",
    referenceImagePath: "C:\\ref.png",
    options,
    pageIndex: 2,
    taskId: "probe-1",
  });
  await bridge.engine.scanSplit.scanOnly({
    pdfPath: "C:\\input.pdf",
    referenceImagePath: "",
    options,
    pageLimit: 30,
    taskId: "scan-only-1",
  });
  await bridge.engine.scanSplit.executeAsync({
    pdfPath: "C:\\input.pdf",
    referenceImagePath: "C:\\ref.png",
    outputDir: "C:\\out",
    prefix: "split_",
    options,
    taskId: "scan-1",
  });

  expect(invoke.mock.calls).toEqual([
    ["engine_call", { method: "scan_split.preview_reference", params: {
      reference_image_path: "C:\\ref.png", nfeatures: 1200, roi: [1, 2, 300, 400],
    }}],
    ["engine_call", { method: "scan_split.probe_page", params: {
      pdf_path: "C:\\input.pdf", reference_image_path: "C:\\ref.png", options,
      page_index: 2, task_id: "probe-1",
    }}],
    ["engine_call", { method: "scan_split.scan_only", params: {
      pdf_path: "C:\\input.pdf", reference_image_path: "", options,
      page_limit: 30, task_id: "scan-only-1",
    }}],
    ["engine_call", { method: "scan_split.execute_async", params: {
      pdf_path: "C:\\input.pdf", reference_image_path: "C:\\ref.png",
      output_dir: "C:\\out", prefix: "split_", options, task_id: "scan-1",
    }}],
  ]);
});
~~~

Run npx vitest run renderer/src/platform/tauri-bridge.test.ts.
Expected: FAIL because the bridge still rejects scan calls.

- [ ] **Step 2: Implement the exact bridge translations**

Replace the four placeholders in tauri-bridge.ts with:

~~~ts
scanSplit: {
  previewReference: (referenceImagePath, opts) =>
    engineCall("scan_split.preview_reference", {
      reference_image_path: referenceImagePath,
      nfeatures: opts?.nfeatures,
      roi: opts?.roi,
    }),
  probePage: (params) =>
    engineCall("scan_split.probe_page", {
      pdf_path: params.pdfPath,
      reference_image_path: params.referenceImagePath,
      options: params.options,
      page_index: params.pageIndex,
      task_id: params.taskId,
    }),
  scanOnly: (params) =>
    engineCall("scan_split.scan_only", {
      pdf_path: params.pdfPath,
      reference_image_path: params.referenceImagePath,
      options: params.options,
      page_limit: params.pageLimit,
      task_id: params.taskId,
    }),
  executeAsync: (params) =>
    engineCall("scan_split.execute_async", {
      pdf_path: params.pdfPath,
      reference_image_path: params.referenceImagePath,
      output_dir: params.outputDir ?? "",
      prefix: params.prefix ?? "",
      options: params.options,
      task_id: params.taskId,
    }),
},
~~~

Run:

~~~powershell
npx vitest run renderer/src/platform/tauri-bridge.test.ts
npm run test:renderer
~~~

Expected: all renderer tests pass and the old NOT_MIGRATED scan assertions are
gone.

- [ ] **Step 3: Enable scan capability**

In runtime.ts, replace the phase-two capability object with:

~~~ts
export const tauriPhaseThreeCapabilities: DesktopCapabilities = {
  rename: true,
  pdfSplit: true,
  scanSplit: true,
  update: false,
};
~~~

Use this object in installDesktopBridge(). Update runtime tests so scan_split
is enabled and sanitization keeps scan_split as scan_split. Keep updater
disabled and Electron capabilities unchanged.

Run:

~~~powershell
npx vitest run renderer/src/platform/runtime.test.ts renderer/src/platform/tauri-bridge.test.ts
npm run typecheck
~~~

Expected: PASS with the Tauri scan panel enabled.

- [ ] **Step 4: Verify the existing scan panel and commit**

The migration must not modify ScanSplitPanel.vue, src/core/pdf_scan_split_engine.py,
or the scan-specific server handlers. Run:

~~~powershell
git diff --check
git diff -- src/core/pdf_scan_split_engine.py engine/server.py electron-app/renderer/src/components/panels/ScanSplitPanel.vue
~~~

Expected: no diff in those files. Commit:

~~~powershell
git add electron-app/renderer/src/platform/tauri-bridge.ts electron-app/renderer/src/platform/runtime.ts electron-app/renderer/src/platform/tauri-bridge.test.ts electron-app/renderer/src/platform/runtime.test.ts
git commit -m "feat: enable Tauri scan split bridge"
~~~

---

### Task 3: Package the Existing Scanner Dependencies in the Tauri Engine

**Files:**
- Modify: engine/tauri_package_profile.py
- Modify: engine/engine-tauri.spec
- Modify: tests/test_tauri_package_profile.py
- Modify: engine/smoke_tauri_engine.py
- Modify: tests/test_smoke_tauri_engine.py
- Modify: electron-app/package.json:20-22

**Interfaces:**
- Consumes: engine/server.py, engine.spec, PyInstaller hooks, and the existing engine resource path.
- Produces: a scan-inclusive engine/dist-tauri/engine.exe and a smoke command that exercises scanner imports and task routes.

- [ ] **Step 1: Write failing package-profile assertions**

Replace the old exclusion test with:

~~~python
from engine.tauri_package_profile import EXCLUDES, HIDDEN_IMPORTS, NATIVE_BINARIES


def test_tauri_package_profile_collects_scan_dependencies():
    assert {
        "src.core.rename_engine",
        "src.core.pdf_split_engine",
        "src.core.pdf_scan_split_engine",
        "src.utils.history_manager",
        "src.utils.path_utils",
        "src.utils.pdf_output",
        "pypdf",
        "fitz",
        "numpy",
        "cv2",
        "zxingcpp",
    } <= set(HIDDEN_IMPORTS)
    assert not {
        "src.core.pdf_scan_split_engine",
        "cv2",
        "numpy",
        "fitz",
        "zxingcpp",
    } & set(EXCLUDES)
    assert isinstance(NATIVE_BINARIES, list)
~~~

Run pytest tests/test_tauri_package_profile.py -v.
Expected: FAIL because the current profile excludes scanner modules.

- [ ] **Step 2: Expand the package profile**

Make tauri_package_profile.py contain:

~~~python
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

HIDDEN_IMPORTS = [
    "src.core.rename_engine",
    "src.core.pdf_split_engine",
    "src.core.pdf_scan_split_engine",
    "src.utils.history_manager",
    "src.utils.path_utils",
    "src.utils.pdf_output",
    "pypdf",
    "fitz",
    "numpy",
    "cv2",
    "zxingcpp",
]

for module in ("cv2", "fitz", "zxingcpp"):
    HIDDEN_IMPORTS.extend(collect_submodules(module))

NATIVE_BINARIES = []
for module in ("cv2", "fitz", "zxingcpp"):
    NATIVE_BINARIES.extend(collect_dynamic_libs(module))

EXCLUDES = []
~~~

Update engine-tauri.spec to import NATIVE_BINARIES and pass it as
binaries=NATIVE_BINARIES. Keep the output name engine, output directory
engine/dist-tauri, and the existing Tauri resource path.

Run pytest tests/test_tauri_package_profile.py -v.
Expected: PASS.

- [ ] **Step 3: Build and inspect collected modules**

Run from electron-app:

~~~powershell
npm run tauri:engine
Test-Path ..\engine\dist-tauri\engine.exe
rg -n "src\.core\.pdf_scan_split_engine|cv2|numpy|fitz|zxingcpp" ..\engine\build-tauri\engine\Analysis-00.toc
~~~

Expected: the executable exists and the analysis table contains scanner
modules. Record its size; the old slim-engine size is no longer a target.

- [ ] **Step 4: Add packaged scanner smoke coverage**

Extend smoke_tauri_engine.py without changing its cleanup behavior. Add
wait_for_task(task_id), which consumes task.progress, task.log, and
task.complete until the matching task completes. Preserve unmatched messages
in a backlog so a fast task cannot be consumed before the waiter starts.

The smoke flow must:

1. generate a QR PNG using zxingcpp.create_barcode() and OpenCV;
2. create a four-page temporary PDF with PyMuPDF and place the QR on two pages;
3. call scan_split.preview_reference and assert ok plus data_url;
4. call scan_split.probe_page and assert the result is marked;
5. call scan_split.scan_only and assert marker_pages is non-empty;
6. call scan_split.execute_async, wait for completion, and assert output PDFs exist;
7. call shutdown and confirm a zero exit code.

Add a fake-queue unit test for wait_for_task(), including a notification that
arrives before its request response.

Run:

~~~powershell
pytest tests/test_smoke_tauri_engine.py -v
npm run tauri:engine:smoke
~~~

Expected: unit tests pass and the packaged executable reports successful
reference preview, probe, quick scan, execution, and shutdown.

- [ ] **Step 5: Commit the scanner package**

Run:

~~~powershell
git diff --check
git add engine/tauri_package_profile.py engine/engine-tauri.spec tests/test_tauri_package_profile.py engine/smoke_tauri_engine.py tests/test_smoke_tauri_engine.py electron-app/package.json
git commit -m "build: package Tauri scan split engine"
~~~

---

### Task 4: Run the Tauri Application and Validate the Scan Workflow

**Files:**
- Modify: README.md:114-135,146-190
- Modify: docs/superpowers/specs/2026-08-20-tauri-scan-split-design.md only if measured commands or limits differ from the accepted design
- Create outside the repository: deterministic PDF and reference fixtures in a temporary directory

**Interfaces:**
- Consumes: the completed Rust boundary, bridge, capability, and packaged-engine tasks.
- Produces: Windows x64 debug and installed NSIS evidence for scan split.

- [ ] **Step 1: Run the automated verification gate**

Run from the worktree root and electron-app:

~~~powershell
..\.venv\Scripts\python.exe -m pytest
npm run test:renderer
npm run typecheck
npm run build
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
npm run tauri:engine
npm run tauri:engine:smoke
npm run tauri:build:debug
~~~

Expected: every command exits 0 before manual acceptance begins.

- [ ] **Step 2: Exercise the Tauri debug workflow**

Run npm run tauri:dev and verify:

1. Scan split is enabled; rename and ordinary PDF split remain enabled.
2. A dialog-selected PDF loads its page count.
3. A PNG reference loads preview and keypoint information.
4. ROI selection retains the selected coordinates.
5. QR, stamp, feature, and auto modes reach the existing engine where the fixture supports them.
6. Quick scan reports marker pages and logs.
7. Full scan execution creates output PDFs in the selected directory.
8. Cancellation leaves the UI idle and preserves already generated outputs.
9. An unselected PDF sent by a test harness returns PATH_NOT_AUTHORIZED before Python execution.
10. Closing Tauri leaves no child process containing the worktree or engine/server.py.

- [ ] **Step 3: Build and smoke-test the scan-inclusive NSIS installer**

Run:

~~~powershell
npm run tauri:package:nsis
~~~

Install the generated per-user package, run one QR scan using the temporary
fixture, verify an output PDF, close the application, and confirm no engine.exe
child remains. The previously completed install/run/uninstall acceptance
remains valid for lifecycle behavior; this is the focused regression check for
the newly embedded scanner dependencies.

- [ ] **Step 4: Record artifacts and the unified limit**

Run:

~~~powershell
Get-Item ..\engine\dist-tauri\engine.exe | Select-Object FullName,Length
Get-Item src-tauri\target\release\app.exe | Select-Object FullName,Length
Get-ChildItem src-tauri\target\release\bundle\nsis -Filter '*.exe' | Select-Object FullName,Length
~~~

Document measured sizes and state that both ordinary PDF split and scan split
accept authorized PDF files up to 1 GiB. Do not change the scan core or claim
support for non-Windows targets.

- [ ] **Step 5: Final review and commit documentation**

Run:

~~~powershell
git diff --check
git status --short
git diff -- src/core/pdf_scan_split_engine.py engine/server.py
~~~

Expected: no scan-core diff, no server scan-handler diff, and only intended
documentation changes. Commit:

~~~powershell
git add README.md docs/superpowers/specs/2026-08-20-tauri-scan-split-design.md docs/superpowers/plans/2026-08-20-tauri-scan-split-migration.md
git commit -m "docs: record Tauri scan split verification"
~~~

---

## Final Verification Checklist

- [ ] src/core/pdf_scan_split_engine.py is unchanged.
- [ ] Scan-specific engine/server.py routes are unchanged.
- [ ] Electron and Tauri PDF input validation both use 1 GiB.
- [ ] All scan input paths are authorized before engine execution.
- [ ] Tauri scan methods are allowlisted and bridge-translated exactly.
- [ ] Scanner dependencies are included in the packaged engine.
- [ ] Python, renderer, Rust, packaging, and smoke tests pass.
- [ ] Debug Tauri scan workflow passes.
- [ ] Scan-inclusive NSIS workflow passes focused installed-app smoke.
- [ ] Non-Windows, updater, and signing remain explicitly out of scope.

