# Tauri Phase One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Windows Tauri shell that shares the existing Vue renderer and Python JSON-RPC engine, with production-shaped support for engine lifecycle, native file access, drag-and-drop, and the complete rename workflow while preserving Electron.

**Architecture:** Keep `electron-app/main` and `electron-app/preload` unchanged as the Electron fallback, add `electron-app/src-tauri` as the Rust shell, and install a Tauri-only TypeScript compatibility bridge before Vue mounts. Rust owns the Python child process and authorized path state; Vue continues consuming the existing `window.engine` and `window.electronAPI` contracts.

**Tech Stack:** Windows x64, Python 3.14.6, Rust 1.97.1 MSVC, Tauri 2.11, Vue 3.5, TypeScript 5.7, Vite 8, Vitest 4, npm 11

**Spec:** `docs/superpowers/specs/2026-08-19-tauri-phase-one-design.md`

## Global Constraints

- Phase one targets Windows `x86_64-pc-windows-msvc` only.
- Electron and Tauri must coexist; existing Electron scripts and runtime behavior remain supported.
- Tauri lives at `electron-app/src-tauri/` and uses `electron-app/renderer/` without copying Vue source.
- Development launches `engine/server.py` with repository-root `.venv/Scripts/python.exe`.
- Packaged engine discovery targets Tauri resources containing `engine.exe`; production bundling is not activated in phase one.
- Enabled engine methods are exactly `ping`, `rename.preview`, `rename.execute`, and `rename.undo`; `shutdown` is internal lifecycle traffic.
- Rename inputs retain the current 500 MiB per-file limit and current file-vs-directory authorization semantics.
- PDF split, scan split, preview protocol, updater, signing, installers, and non-Windows targets remain out of scope.
- Every hand-written behavior follows red-green-refactor. Generated scaffold, manifests, lockfiles, and Tauri configuration are configuration exceptions verified by metadata and build commands.
- Do not rewrite Python business logic or remove Electron during this plan.

## File Map

| Path | Responsibility |
|---|---|
| `.venv/` | Ignored Python 3.14 environment used by tests and Tauri development |
| `electron-app/src-tauri/src/engine/error.rs` | Stable serializable desktop error codes |
| `electron-app/src-tauri/src/engine/protocol.rs` | JSON-RPC line encoding and classification |
| `electron-app/src-tauri/src/engine/manager.rs` | Python process lifecycle, pending requests, timeouts, notifications |
| `electron-app/src-tauri/src/commands/engine.rs` | Tauri engine commands, allowlist, path validation, engine discovery |
| `electron-app/src-tauri/src/commands/files.rs` | Authorized path state, dialogs, metadata, native drop registration |
| `electron-app/renderer/src/platform/tauri-bridge.ts` | Tauri invoke/event adapter implementing existing browser globals |
| `electron-app/renderer/src/platform/runtime.ts` | Runtime detection and phase-one capability policy |
| `electron-app/renderer/src/platform/*.test.ts` | Bridge and capability behavior tests |
| `electron-app/renderer/src/components/SideNav.vue` | Disable capabilities not migrated in Tauri |
| `electron-app/renderer/src/components/panels/RenamePanel.vue` | Consume native Tauri file drops in addition to Electron HTML drops |

---

### Task 1: Establish the Python Baseline and Tauri Scaffold

**Files:**
- Modify: `electron-app/package.json`
- Modify: `electron-app/package-lock.json`
- Modify: `electron-app/tsconfig.json`
- Create: `electron-app/src-tauri/Cargo.toml`
- Create: `electron-app/src-tauri/Cargo.lock`
- Create: `electron-app/src-tauri/build.rs`
- Create: `electron-app/src-tauri/tauri.conf.json`
- Create: `electron-app/src-tauri/capabilities/default.json`
- Create: generated Tauri icons under `electron-app/src-tauri/icons/`
- Create: `electron-app/src-tauri/src/main.rs`

**Interfaces:**
- Consumes: existing `requirements.txt`, renderer dev server at `http://localhost:5173`, renderer output at `electron-app/dist/renderer`
- Produces: reproducible `.venv`, npm/Cargo dependency locks, `npm run tauri:dev`, `npm run tauri:build:debug`, compilable empty Tauri application

- [ ] **Step 1: Create the repository-local Python environment**

Run from the worktree root:

```powershell
py -3.14 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
```

Expected: all dependencies install into `.venv`; no repository file becomes tracked.

- [ ] **Step 2: Verify the Python and Electron baselines before migration**

```powershell
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
Set-Location electron-app
npm ci
npm run typecheck
npm run build
```

Expected: Python tests pass, Vue type checking passes, and Electron renderer/main/preload builds pass. If any baseline fails, stop and report it before migration work.

- [ ] **Step 3: Add pinned Tauri and test dependencies**

```powershell
npm install --save-exact @tauri-apps/api@2.11.1
npm install --save-dev --save-exact @tauri-apps/cli@2.11.4 vitest@4.1.11 @vue/test-utils@2.4.11 happy-dom@20.11.2
```

Add these scripts to `electron-app/package.json`:

```json
{
  "scripts": {
    "test:renderer": "vitest run",
    "tauri": "tauri",
    "tauri:dev": "tauri dev",
    "tauri:build:debug": "tauri build --debug --no-bundle"
  }
}
```

- [ ] **Step 4: Generate the Tauri scaffold without touching Electron files**

Run from `electron-app/`:

```powershell
npm run tauri -- init --ci --app-name 'File Toolbox' --window-title 'File Toolbox' --frontend-dist '../dist/renderer' --dev-url 'http://localhost:5173' --before-dev-command 'npm run dev:renderer' --before-build-command 'npm run build:renderer'
```

If the generator creates `src/lib.rs`, remove that generated file and keep the Windows-only application bootstrap in `src/main.rs`, matching the approved design.

Set `src-tauri/tauri.conf.json` to the following phase-one shape while preserving generated schema fields and icon paths:

```json
{
  "productName": "File Toolbox",
  "version": "2.5.0",
  "identifier": "com.filetoolbox.app",
  "build": {
    "beforeDevCommand": "npm run dev:renderer",
    "devUrl": "http://localhost:5173",
    "beforeBuildCommand": "npm run build:renderer",
    "frontendDist": "../dist/renderer"
  },
  "app": {
    "windows": [{
      "label": "main",
      "title": "File Toolbox",
      "width": 1280,
      "height": 820,
      "minWidth": 1120,
      "minHeight": 720,
      "dragDropEnabled": true
    }],
    "security": { "csp": "default-src 'self'; connect-src ipc: http://ipc.localhost; img-src 'self' data: blob:" }
  },
  "bundle": { "active": false }
}
```

Set `src-tauri/capabilities/default.json` to:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "File Toolbox phase-one desktop capability",
  "windows": ["main"],
  "permissions": ["core:default"]
}
```

Set Rust dependencies in `src-tauri/Cargo.toml`:

```toml
[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tauri = { version = "2.11.5", features = [] }
tauri-plugin-dialog = "2.7.2"
tokio = { version = "1", features = ["io-util", "macros", "process", "rt-multi-thread", "sync", "time"] }
uuid = { version = "1", features = ["v4"] }

[dev-dependencies]
tempfile = "3"
```

- [ ] **Step 5: Extend TypeScript compilation to platform code and tests**

Update `electron-app/tsconfig.json` include/exclude fields to:

```json
{
  "include": [
    "main/**/*.ts",
    "preload/**/*.ts",
    "renderer/src/**/*.ts",
    "renderer/src/**/*.vue"
  ],
  "exclude": ["node_modules", "dist", "src-tauri/target"]
}
```

- [ ] **Step 6: Validate generated configuration**

```powershell
npm run tauri -- info
& "$env:USERPROFILE\.cargo\bin\cargo.exe" check --manifest-path src-tauri/Cargo.toml
npm run tauri:build:debug
```

Expected: Tauri reports WebView2/MSVC/Rust as available, Cargo checks successfully, and the empty shared-renderer Tauri debug build succeeds.

- [ ] **Step 7: Commit the reproducible scaffold**

```powershell
git add electron-app/package.json electron-app/package-lock.json electron-app/tsconfig.json electron-app/src-tauri
git commit -m "build: scaffold Tauri desktop shell"
```

---

### Task 2: Implement JSON-RPC Protocol Types and Stable Errors

**Files:**
- Create: `electron-app/src-tauri/src/engine/mod.rs`
- Create: `electron-app/src-tauri/src/engine/error.rs`
- Create: `electron-app/src-tauri/src/engine/protocol.rs`
- Modify: `electron-app/src-tauri/src/main.rs`

**Interfaces:**
- Consumes: newline-delimited JSON emitted by `engine/server.py`
- Produces: `DesktopError`, `IncomingMessage`, `parse_line(&str)`, and `encode_request(u64, &str, Value, &str)`

- [ ] **Step 1: Write protocol tests before the module exists**

Add tests at the bottom of `protocol.rs` describing the required API:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_ready_notification() {
        assert_eq!(
            parse_line(r#"{"jsonrpc":"2.0","method":"ready","params":{}}"#).unwrap(),
            IncomingMessage::Ready,
        );
    }

    #[test]
    fn parses_success_and_error_responses() {
        assert_eq!(
            parse_line(r#"{"jsonrpc":"2.0","id":7,"result":{"pong":true}}"#).unwrap(),
            IncomingMessage::Response { id: 7, result: Ok(json!({"pong": true})) },
        );
        let message = parse_line(r#"{"jsonrpc":"2.0","id":8,"error":{"code":-32000,"message":"boom"}}"#).unwrap();
        assert!(matches!(message, IncomingMessage::Response { id: 8, result: Err(_) }));
    }

    #[test]
    fn parses_engine_notification() {
        assert_eq!(
            parse_line(r#"{"jsonrpc":"2.0","method":"task.progress","params":{"current":1}}"#).unwrap(),
            IncomingMessage::Notification {
                method: "task.progress".into(),
                params: json!({"current": 1}),
            },
        );
    }

    #[test]
    fn rejects_malformed_or_non_object_json() {
        assert!(parse_line("not-json").is_err());
        assert!(parse_line("[]").is_err());
    }

    #[test]
    fn encodes_request_with_auth_and_finite_json() {
        let line = encode_request(3, "ping", json!({}), "token").unwrap();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&line).unwrap(),
            json!({"jsonrpc":"2.0","id":3,"method":"ping","params":{},"auth":"token"}),
        );
    }
}
```

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml engine::protocol -- --nocapture
```

Expected: compilation fails because `IncomingMessage`, `parse_line`, and `encode_request` do not exist.

- [ ] **Step 3: Implement stable errors and protocol classification**

Define in `error.rs`:

```rust
#[derive(Debug, Clone, serde::Serialize, PartialEq, Eq)]
pub struct DesktopError {
    pub code: &'static str,
    pub message: String,
}

impl DesktopError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self { code, message: message.into() }
    }

    pub fn protocol(message: impl Into<String>) -> Self {
        Self::new("ENGINE_PROTOCOL_ERROR", message)
    }
}

impl std::fmt::Display for DesktopError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.message)
    }
}

impl std::error::Error for DesktopError {}
```

Define in `protocol.rs`:

```rust
#[derive(Debug, Clone, PartialEq)]
pub enum IncomingMessage {
    Ready,
    Response { id: u64, result: Result<serde_json::Value, DesktopError> },
    Notification { method: String, params: serde_json::Value },
}

pub fn parse_line(line: &str) -> Result<IncomingMessage, DesktopError> {
    let value: serde_json::Value = serde_json::from_str(line)
        .map_err(|error| DesktopError::protocol(format!("无法解析引擎消息: {error}")))?;
    let object = value.as_object()
        .ok_or_else(|| DesktopError::protocol("引擎消息必须是 JSON 对象"))?;

    if let Some(id) = object.get("id").and_then(serde_json::Value::as_u64) {
        if let Some(error) = object.get("error") {
            let message = error.get("message").and_then(serde_json::Value::as_str).unwrap_or("Engine error");
            return Ok(IncomingMessage::Response {
                id,
                result: Err(DesktopError::new("ENGINE_REQUEST_FAILED", message)),
            });
        }
        return Ok(IncomingMessage::Response {
            id,
            result: Ok(object.get("result").cloned().unwrap_or(serde_json::Value::Null)),
        });
    }

    let method = object.get("method").and_then(serde_json::Value::as_str)
        .ok_or_else(|| DesktopError::protocol("引擎通知缺少 method"))?;
    if method == "ready" {
        return Ok(IncomingMessage::Ready);
    }
    Ok(IncomingMessage::Notification {
        method: method.to_owned(),
        params: object.get("params").cloned().unwrap_or_else(|| serde_json::json!({})),
    })
}

pub fn encode_request(id: u64, method: &str, params: serde_json::Value, auth: &str) -> Result<String, DesktopError> {
    serde_json::to_string(&serde_json::json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": method,
        "params": params,
        "auth": auth,
    })).map_err(|error| DesktopError::protocol(format!("无法编码引擎请求: {error}")))
}
```

- [ ] **Step 4: Run tests and format**

```powershell
& "$env:USERPROFILE\.cargo\bin\cargo.exe" fmt --manifest-path src-tauri/Cargo.toml -- --check
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml engine::protocol -- --nocapture
```

Expected: all protocol tests pass.

- [ ] **Step 5: Commit the protocol boundary**

```powershell
git add electron-app/src-tauri/src/engine electron-app/src-tauri/src/main.rs
git commit -m "feat: add Tauri engine protocol types"
```

---

### Task 3: Build the Tested Python Engine Manager

**Files:**
- Create: `electron-app/src-tauri/src/engine/manager.rs`
- Create: `electron-app/src-tauri/tests/fixtures/fake_engine.py`
- Modify: `electron-app/src-tauri/src/engine/mod.rs`

**Interfaces:**
- Consumes: `DesktopError`, `IncomingMessage`, `parse_line`, `encode_request`
- Produces: `EngineConfig`, `EngineStatus`, `EngineNotification`, `EngineEventSink`, and async `EngineManager::{start,status,call,restart,shutdown}`

- [ ] **Step 1: Add a deterministic fake JSON-RPC engine fixture**

Create `tests/fixtures/fake_engine.py`:

```python
import json
import os
import sys
import time

print(json.dumps({"jsonrpc": "2.0", "method": "ready", "params": {}}), flush=True)
for raw_line in sys.stdin:
    request = json.loads(raw_line)
    method = request.get("method")
    request_id = request.get("id")
    if method == "ping":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"pong": True}}), flush=True)
    elif method == "emit_test":
        print(json.dumps({"jsonrpc": "2.0", "method": "task.progress", "params": {"current": 1}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"emitted": True}}), flush=True)
    elif method == "hang":
        time.sleep(30)
    elif method == "crash":
        os._exit(17)
    elif method == "shutdown":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}), flush=True)
        break
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}), flush=True)
```

- [ ] **Step 2: Write manager tests against the fixture**

Add tests in `manager.rs` using this public shape:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{path::PathBuf, sync::{Arc, Mutex}, time::Duration};

    #[derive(Default)]
    struct RecordingSink(Mutex<Vec<EngineNotification>>);

    impl EngineEventSink for RecordingSink {
        fn emit(&self, notification: EngineNotification) {
            self.0.lock().unwrap().push(notification);
        }
    }

    fn fixture_config() -> EngineConfig {
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        EngineConfig {
            program: manifest.join("../../.venv/Scripts/python.exe"),
            args: vec![manifest.join("tests/fixtures/fake_engine.py").into_os_string()],
            cwd: manifest.join("../.."),
            auth_token: "test-token".into(),
            debug_errors: true,
        }
    }

    #[tokio::test]
    async fn starts_calls_and_shuts_down() {
        let manager = EngineManager::new(fixture_config(), Arc::new(RecordingSink::default()));
        manager.start().await.unwrap();
        assert_eq!(manager.status().await, EngineStatus::Ready);
        assert_eq!(manager.call("ping", json!({}), Duration::from_secs(2)).await.unwrap(), json!({"pong": true}));
        manager.shutdown().await.unwrap();
        assert_eq!(manager.status().await, EngineStatus::Stopped);
    }

    #[tokio::test]
    async fn forwards_notifications_and_times_out_requests() {
        let sink = Arc::new(RecordingSink::default());
        let manager = EngineManager::new(fixture_config(), sink.clone());
        manager.start().await.unwrap();
        manager.call("emit_test", json!({}), Duration::from_secs(2)).await.unwrap();
        assert_eq!(sink.0.lock().unwrap()[0].method, "task.progress");
        let error = manager.call("hang", json!({}), Duration::from_millis(50)).await.unwrap_err();
        assert_eq!(error.code, "ENGINE_TIMEOUT");
        manager.shutdown().await.unwrap();
    }

    #[tokio::test]
    async fn crash_marks_engine_failed_and_restart_recovers() {
        let manager = EngineManager::new(fixture_config(), Arc::new(RecordingSink::default()));
        manager.start().await.unwrap();
        let _ = manager.call("crash", json!({}), Duration::from_secs(2)).await;
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert!(matches!(manager.status().await, EngineStatus::Failed(_)));
        manager.restart().await.unwrap();
        assert_eq!(manager.call("ping", json!({}), Duration::from_secs(2)).await.unwrap(), json!({"pong": true}));
        manager.shutdown().await.unwrap();
    }
}
```

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml engine::manager -- --nocapture
```

Expected: compilation fails because manager types and methods do not exist.

- [ ] **Step 4: Implement the manager state and interfaces**

Use these exact public declarations:

```rust
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
#[serde(tag = "status", content = "error", rename_all = "lowercase")]
pub enum EngineStatus { Stopped, Starting, Ready, Failed(String), Stopping }

#[derive(Debug, Clone)]
pub struct EngineConfig {
    pub program: std::path::PathBuf,
    pub args: Vec<std::ffi::OsString>,
    pub cwd: std::path::PathBuf,
    pub auth_token: String,
    pub debug_errors: bool,
}

#[derive(Debug, Clone, serde::Serialize, PartialEq)]
pub struct EngineNotification { pub method: String, pub params: serde_json::Value }

pub trait EngineEventSink: Send + Sync + 'static {
    fn emit(&self, notification: EngineNotification);
}

#[derive(Clone)]
pub struct EngineManager {
    config: EngineConfig,
    sink: std::sync::Arc<dyn EngineEventSink>,
    state: std::sync::Arc<tokio::sync::Mutex<EngineStatus>>,
    lifecycle: std::sync::Arc<tokio::sync::Mutex<()>>,
    child: std::sync::Arc<tokio::sync::Mutex<Option<tokio::process::Child>>>,
    stdin: std::sync::Arc<tokio::sync::Mutex<Option<tokio::process::ChildStdin>>>,
    pending: std::sync::Arc<tokio::sync::Mutex<std::collections::HashMap<u64, tokio::sync::oneshot::Sender<Result<serde_json::Value, DesktopError>>>>>,
    next_id: std::sync::Arc<std::sync::atomic::AtomicU64>,
}

impl EngineManager {
    pub fn new(config: EngineConfig, sink: std::sync::Arc<dyn EngineEventSink>) -> Self;
    pub async fn status(&self) -> EngineStatus;
    pub async fn start(&self) -> Result<(), DesktopError>;
    pub async fn call(&self, method: &str, params: serde_json::Value, timeout: std::time::Duration) -> Result<serde_json::Value, DesktopError>;
    pub async fn restart(&self) -> Result<(), DesktopError>;
    pub async fn shutdown(&self) -> Result<(), DesktopError>;
}
```

Implementation rules:

1. Store state, child, stdin, and pending response senders behind Tokio mutexes.
2. Spawn Python with piped stdin/stdout/stderr, `FILE_TOOLBOX_ENGINE_TOKEN`, and `FILE_TOOLBOX_ENGINE_DEBUG_ERRORS`.
3. On Windows, build a `std::process::Command`, apply `CREATE_NO_WINDOW`, then convert it to `tokio::process::Command`.
4. Start one stdout line-reader task and one stderr logging task.
5. Resolve startup only after `IncomingMessage::Ready`, with a 30-second timeout.
6. Serialize stdin writes, register a oneshot sender before writing, and remove it on write failure or timeout.
7. Resolve responses by ID; emit notifications through `EngineEventSink`.
8. On stdout closure or child exit, reject all pending calls with `ENGINE_EXITED` and set `Failed` unless shutdown is active.
9. `restart` holds one lifecycle mutex across shutdown/start so concurrent calls cannot create duplicate children.
10. `shutdown` sends a two-second `shutdown` request, waits up to 1.5 seconds for exit, then kills and waits for the child.

- [ ] **Step 5: Verify GREEN and run all Rust tests**

```powershell
& "$env:USERPROFILE\.cargo\bin\cargo.exe" fmt --manifest-path src-tauri/Cargo.toml
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml -- --nocapture
```

Expected: manager and protocol tests pass with no orphan fixture process.

- [ ] **Step 6: Commit the engine manager**

```powershell
git add electron-app/src-tauri/src/engine electron-app/src-tauri/tests/fixtures/fake_engine.py
git commit -m "feat: manage Python engine from Tauri"
```

---

### Task 4: Expose Safe Tauri Engine Commands

**Files:**
- Create: `electron-app/src-tauri/src/commands/mod.rs`
- Create: `electron-app/src-tauri/src/commands/engine.rs`
- Modify: `electron-app/src-tauri/src/main.rs`

**Interfaces:**
- Consumes: `EngineManager`, Tauri managed state and event emission
- Produces: `engine_status`, `engine_call`, `engine_restart`, development/packaged engine resolution, allowlisted method timeouts

- [ ] **Step 1: Write allowlist, timeout, and engine-discovery tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::{path::PathBuf, time::Duration};

    #[test]
    fn phase_one_allowlist_is_exact() {
        for method in ["ping", "rename.preview", "rename.execute", "rename.undo"] {
            assert!(is_allowed_method(method));
        }
        for method in ["shutdown", "pdf_split.preview", "scan_split.execute_async", "history.clear"] {
            assert!(!is_allowed_method(method));
        }
    }

    #[test]
    fn rename_execute_uses_long_timeout() {
        assert_eq!(method_timeout("rename.execute"), Duration::from_secs(300));
        assert_eq!(method_timeout("ping"), Duration::from_secs(120));
    }

    #[test]
    fn development_config_uses_repo_venv() {
        let root = PathBuf::from(r"C:\repo\file-toolbox");
        let config = development_engine_config(&root, "token");
        assert_eq!(config.program, root.join(r".venv\Scripts\python.exe"));
        assert_eq!(config.args, vec![root.join(r"engine\server.py").into_os_string()]);
    }
}
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml commands::engine -- --nocapture
```

Expected: compilation fails because engine command helpers do not exist.

- [ ] **Step 3: Implement state, allowlist, discovery, and commands**

Define:

```rust
pub struct AppState {
    pub engine: std::sync::Arc<EngineManager>,
}

#[derive(serde::Serialize)]
pub struct EngineStatusResponse {
    pub status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

pub fn is_allowed_method(method: &str) -> bool {
    matches!(method, "ping" | "rename.preview" | "rename.execute" | "rename.undo")
}

pub fn method_timeout(method: &str) -> std::time::Duration {
    if method == "rename.execute" { std::time::Duration::from_secs(300) }
    else { std::time::Duration::from_secs(120) }
}

#[tauri::command]
pub async fn engine_status(state: tauri::State<'_, AppState>) -> EngineStatusResponse;

#[tauri::command]
pub async fn engine_call(
    method: String,
    params: serde_json::Value,
    state: tauri::State<'_, AppState>,
) -> Result<serde_json::Value, DesktopError>;

#[tauri::command]
pub async fn engine_restart(state: tauri::State<'_, AppState>) -> Result<(), DesktopError>;
```

`engine_call` must reject non-allowlisted methods with `METHOD_NOT_ALLOWED` and select the timeout through `method_timeout`. Task 5 adds authorized-path validation before forwarding rename methods. Map internal `Stopped`, `Failed`, and `Stopping` states to `{ status: "error", error }`; map `Starting` and `Ready` to the existing frontend status strings.

Implement `TauriEventSink` with `tauri::Emitter` and event name `engine-notification`. Emit an additional `engine.status` notification after ready, failure, and restart transitions.

Resolve engine configuration in `setup`:

- debug builds: repository root derived from `CARGO_MANIFEST_DIR/../..`;
- release builds: `app.path().resource_dir()?.join("engine/engine.exe")`;
- generate the authentication token with `uuid::Uuid::new_v4().simple().to_string()` and pass the same token only to Rust/Python environment state.

Register state and commands in `main.rs`, start the engine asynchronously during setup, and call graceful shutdown on `RunEvent::ExitRequested` before allowing process exit.

- [ ] **Step 4: Run tests, lint, and a command-layer check**

```powershell
& "$env:USERPROFILE\.cargo\bin\cargo.exe" fmt --manifest-path src-tauri/Cargo.toml
& "$env:USERPROFILE\.cargo\bin\cargo.exe" clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml -- --nocapture
```

Expected: allowlist/discovery tests pass and Clippy reports no warnings.

- [ ] **Step 5: Commit safe engine commands**

```powershell
git add electron-app/src-tauri/src/commands electron-app/src-tauri/src/main.rs electron-app/src-tauri/Cargo.toml electron-app/src-tauri/Cargo.lock
git commit -m "feat: expose safe Tauri engine commands"
```

---

### Task 5: Implement Authorized File Access and Native Drops

**Files:**
- Create: `electron-app/src-tauri/src/commands/files.rs`
- Modify: `electron-app/src-tauri/src/commands/engine.rs`
- Modify: `electron-app/src-tauri/src/main.rs`

**Interfaces:**
- Consumes: native paths selected or dropped through Tauri
- Produces: `PathAuthorizer`, `open_files`, `open_directory`, `stat_paths`, `validate_rename_params`, and `desktop-file-drop` events

- [ ] **Step 1: Write authorization and metadata tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn file_authorization_does_not_authorize_siblings() {
        let temp = tempfile::tempdir().unwrap();
        let allowed = temp.path().join("allowed.txt");
        let sibling = temp.path().join("sibling.txt");
        std::fs::write(&allowed, b"ok").unwrap();
        std::fs::write(&sibling, b"no").unwrap();
        let auth = PathAuthorizer::default();
        auth.authorize_file(&allowed).unwrap();
        assert!(auth.is_authorized(&allowed));
        assert!(!auth.is_authorized(&sibling));
    }

    #[test]
    fn directory_authorization_includes_descendants_only() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("root");
        let child = root.join("child.txt");
        std::fs::create_dir(&root).unwrap();
        std::fs::write(&child, b"ok").unwrap();
        let auth = PathAuthorizer::default();
        auth.authorize_directory(&root).unwrap();
        assert!(auth.is_authorized(&child));
        assert!(!auth.is_authorized(temp.path().join("outside.txt")));
    }

    #[test]
    fn rename_validation_requires_authorized_files_and_output_directory() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("a.txt");
        let output = temp.path().join("out");
        std::fs::write(&source, b"a").unwrap();
        std::fs::create_dir(&output).unwrap();
        let auth = PathAuthorizer::default();
        assert_eq!(validate_rename_params("rename.execute", &json!({"files":[source],"output_dir":output}), &auth).unwrap_err().code, "PATH_NOT_AUTHORIZED");
    }
}
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml commands::files -- --nocapture
```

Expected: compilation fails because `PathAuthorizer` and validation functions do not exist.

- [ ] **Step 3: Implement canonical authorization with bounded state**

Use these public types and constants:

```rust
const MAX_AUTHORIZED_PATHS: usize = 12_000;
const MAX_GENERIC_INPUT_FILE_SIZE: u64 = 500 * 1024 * 1024;

#[derive(Clone)]
enum AuthorizedPathKind { File, Directory }

#[derive(Clone)]
struct AuthorizedPath {
    path: std::path::PathBuf,
    kind: AuthorizedPathKind,
}

#[derive(Default)]
pub struct PathAuthorizer {
    entries: std::sync::Mutex<std::collections::VecDeque<AuthorizedPath>>,
}

pub struct FileState {
    pub paths: std::sync::Arc<PathAuthorizer>,
}

impl PathAuthorizer {
    pub fn authorize_file(&self, path: impl AsRef<std::path::Path>) -> Result<std::path::PathBuf, DesktopError>;
    pub fn authorize_directory(&self, path: impl AsRef<std::path::Path>) -> Result<std::path::PathBuf, DesktopError>;
    pub fn is_authorized(&self, path: impl AsRef<std::path::Path>) -> bool;
}

pub fn validate_rename_params(
    method: &str,
    params: &serde_json::Value,
    authorizer: &PathAuthorizer,
) -> Result<(), DesktopError>;
```

Canonicalize existing paths. File grants match only the exact case-insensitive Windows path; directory grants match the directory and descendants using `Path::strip_prefix`, never string prefix matching. Evict oldest grants beyond 12,000. Validate every `files[]` entry is an authorized regular file no larger than 500 MiB and validate non-empty `output_dir` as an authorized directory.

- [ ] **Step 4: Implement Rust dialog and stat commands**

Use `tauri_plugin_dialog::DialogExt` in async Tauri commands and call the blocking picker only inside `tauri::async_runtime::spawn_blocking`.

```rust
#[derive(Debug, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FileFilter {
    pub name: String,
    pub extensions: Vec<String>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OpenFilesOptions {
    pub filters: Option<Vec<FileFilter>>,
    pub multi: Option<bool>,
    pub title: Option<String>,
}

#[derive(Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FilePathStat {
    pub path: String,
    pub is_file: bool,
    pub is_directory: bool,
    pub size: u64,
}

#[derive(Debug, serde::Serialize)]
pub struct FileAccessError {
    pub code: &'static str,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

#[derive(Debug, serde::Serialize)]
#[serde(untagged)]
pub enum FileAccessResult<T> {
    Success { ok: bool, value: T },
    Failure { ok: bool, error: FileAccessError },
}

#[tauri::command]
pub async fn open_files(app: tauri::AppHandle, options: Option<OpenFilesOptions>, state: tauri::State<'_, FileState>) -> Result<Vec<String>, DesktopError>;

#[tauri::command]
pub async fn open_directory(app: tauri::AppHandle, title: Option<String>, state: tauri::State<'_, FileState>) -> Result<String, DesktopError>;

#[tauri::command]
pub async fn stat_paths(paths: Vec<String>, state: tauri::State<'_, FileState>) -> Vec<FileAccessResult<FilePathStat>>;
```

Convert `tauri_plugin_dialog::FilePath` with `into_path()`, authorize each selected result, and serialize Windows paths losslessly with `to_string_lossy().into_owned()`. Construct `FileAccessResult::Success` with `ok: true` and `FileAccessResult::Failure` with `ok: false`. Mirror existing error codes and `FilePathStat` camelCase fields.

Manage `FileState` separately from `AppState`. Update `engine_call` to accept both states and invoke `validate_rename_params(&method, &params, &files.paths)` before `EngineManager::call`.

- [ ] **Step 5: Register native drop paths before emitting them**

In the Tauri run-event callback, handle:

```rust
tauri::RunEvent::WindowEvent {
    event: tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }),
    ..
} => {
    use tauri::{Emitter, Manager};
    let files = app_handle.state::<FileState>();
    let authorized = paths.iter()
        .filter_map(|path| files.paths.authorize_file(path).ok())
        .map(|path| path.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    if !authorized.is_empty() {
        let _ = app_handle.emit("desktop-file-drop", authorized);
    }
}
```

Include a wildcard match because Tauri drag/drop events are non-exhaustive. Register `open_files`, `open_directory`, and `stat_paths` in `generate_handler!`.

- [ ] **Step 6: Verify path safety and Rust quality**

```powershell
& "$env:USERPROFILE\.cargo\bin\cargo.exe" fmt --manifest-path src-tauri/Cargo.toml
& "$env:USERPROFILE\.cargo\bin\cargo.exe" clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml -- --nocapture
```

Expected: authorization boundary, metadata, engine protocol, and manager tests all pass.

- [ ] **Step 7: Commit native file access**

```powershell
git add electron-app/src-tauri/src/commands electron-app/src-tauri/src/main.rs
git commit -m "feat: authorize Tauri file access"
```

---

### Task 6: Install the Tested TypeScript Compatibility Bridge

**Files:**
- Create: `electron-app/renderer/src/platform/runtime.ts`
- Create: `electron-app/renderer/src/platform/tauri-bridge.ts`
- Create: `electron-app/renderer/src/platform/tauri-bridge.test.ts`
- Modify: `electron-app/renderer/src/main.ts`
- Modify: `electron-app/renderer/src/env.d.ts`
- Modify: `electron-app/shared/api-types.ts`

**Interfaces:**
- Consumes: Tauri `invoke`, `listen`, and `desktop-file-drop`
- Produces: `installDesktopBridge()`, Tauri implementations of `EngineAPI` and `ElectronAPI`, `DesktopCapabilities`, and unchanged Electron globals

- [ ] **Step 1: Write bridge translation tests with injected dependencies**

```typescript
import { describe, expect, it, vi } from "vitest";
import { createTauriBridge } from "./tauri-bridge";

describe("Tauri compatibility bridge", () => {
  it("translates rename calls exactly", async () => {
    const invoke = vi.fn().mockResolvedValue([]);
    const bridge = createTauriBridge({ invoke, listen: vi.fn() });
    await bridge.engine.rename.preview(["C:\\a.txt"], [{ type: "uniform_name", base_name: "b" }]);
    expect(invoke).toHaveBeenCalledWith("engine_call", {
      method: "rename.preview",
      params: { files: ["C:\\a.txt"], rules: [{ type: "uniform_name", base_name: "b" }] },
    });
  });

  it("maps file dialogs and restart to Rust commands", async () => {
    const invoke = vi.fn().mockResolvedValue([]);
    const bridge = createTauriBridge({ invoke, listen: vi.fn() });
    await bridge.electron.openFileDialog({ multi: true });
    await bridge.electron.restartEngine();
    expect(invoke).toHaveBeenNthCalledWith(1, "open_files", { options: { multi: true } });
    expect(invoke).toHaveBeenNthCalledWith(2, "engine_restart");
  });

  it("unsubscribes notifications even when listen resolves later", async () => {
    const unlisten = vi.fn();
    const listen = vi.fn().mockResolvedValue(unlisten);
    const bridge = createTauriBridge({ invoke: vi.fn(), listen });
    const dispose = bridge.engine.onNotification(() => undefined);
    dispose();
    await Promise.resolve();
    expect(unlisten).toHaveBeenCalledOnce();
  });

  it("returns explicit unsupported update state", async () => {
    const bridge = createTauriBridge({ invoke: vi.fn(), listen: vi.fn() });
    await expect(bridge.electron.update.getStatus()).resolves.toMatchObject({ state: "unsupported", supported: false });
  });

  it("forwards native file-drop paths and cleans up", async () => {
    const unlisten = vi.fn();
    const listen = vi.fn(async (_name, handler) => {
      handler({ payload: ["C:\\dropped.txt"] });
      return unlisten;
    });
    const bridge = createTauriBridge({ invoke: vi.fn(), listen });
    const callback = vi.fn();
    const dispose = bridge.electron.onFileDrop?.(callback);
    await Promise.resolve();
    expect(callback).toHaveBeenCalledWith(["C:\\dropped.txt"]);
    dispose?.();
    await Promise.resolve();
    expect(unlisten).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run Vitest and verify RED**

```powershell
npm run test:renderer -- renderer/src/platform/tauri-bridge.test.ts
```

Expected: test collection fails because `tauri-bridge.ts` does not exist.

- [ ] **Step 3: Add runtime and capability types**

Define in `runtime.ts`:

```typescript
export type DesktopCapabilities = {
  rename: boolean;
  pdfSplit: boolean;
  scanSplit: boolean;
  update: boolean;
};

export const electronCapabilities: DesktopCapabilities = {
  rename: true, pdfSplit: true, scanSplit: true, update: true,
};

export const tauriPhaseOneCapabilities: DesktopCapabilities = {
  rename: true, pdfSplit: false, scanSplit: false, update: false,
};
```

Add optional `onFileDrop` to `ElectronAPI`:

```typescript
onFileDrop?: (callback: (paths: string[]) => void) => () => void;
```

Add `desktopRuntime` to `Window` in `env.d.ts`:

```typescript
desktopRuntime?: { kind: "electron" | "tauri"; capabilities: DesktopCapabilities };
```

- [ ] **Step 4: Implement the factory and installer**

`createTauriBridge(deps)` returns `{ engine, electron }` and accepts injected functions so tests never depend on a live Tauri webview:

```typescript
type InvokeFn = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type ListenFn = <T>(event: string, handler: (event: { payload: T }) => void) => Promise<() => void>;

export function createTauriBridge(deps: { invoke: InvokeFn; listen: ListenFn }): {
  engine: EngineAPI;
  electron: ElectronAPI;
};
```

Use invoke command names and parameter shapes from the Rust tasks.

Required engine mappings:

```typescript
status -> invoke("engine_status")
ping -> invoke("engine_call", { method: "ping", params: {} })
rename.preview -> engine_call("rename.preview", { files, rules })
rename.execute -> engine_call("rename.execute", { files, rules, save_method, output_dir })
rename.undo -> engine_call("rename.undo", { undo_token })
```

`onNotification` listens for `engine-notification`. `onFileDrop` listens for `desktop-file-drop`. Both return synchronous cleanup functions that deactivate callbacks immediately and call the eventual asynchronous unlisten function.

PDF, scan, and cancellation methods reject `{ code: "NOT_MIGRATED", message: "该功能尚未迁移到 Tauri" }`. History `get` resolves `{ records: [], session_id: "" }`; history `clear` resolves `{ cleared: false, session_id: "", error: "该功能尚未迁移到 Tauri" }` so the About panel can render without an unhandled rejection.

Return this exact update status from `getStatus`, `check`, and `download`:

```typescript
const unsupportedUpdateStatus: AppUpdateStatus = {
  state: "unsupported",
  supported: false,
  packageType: "development",
  portable: false,
  current: "2.5.0",
};
```

`install` resolves `{ accepted: false, status: unsupportedUpdateStatus }`; `onStatus` returns a no-op disposer. `readDirFiles` and `getFilePreviewUrl` return structured `unsupported_type` failures. `openExternal`, `openDataDir`, and `saveFile` reject the same `NOT_MIGRATED` object. `getPathForFile` returns an empty string and `getPathsForFiles` returns an empty list because Tauri native drops use `desktop-file-drop`.

Implement:

```typescript
export async function installDesktopBridge(): Promise<void> {
  const { isTauri, invoke } = await import("@tauri-apps/api/core");
  if (!isTauri()) {
    window.desktopRuntime = { kind: "electron", capabilities: electronCapabilities };
    return;
  }
  const { listen } = await import("@tauri-apps/api/event");
  const bridge = createTauriBridge({ invoke, listen });
  window.engine = bridge.engine;
  window.electronAPI = bridge.electron;
  window.desktopRuntime = { kind: "tauri", capabilities: tauriPhaseOneCapabilities };
}
```

Update `main.ts` so Vue mounts only after bridge installation resolves; on installation failure, log once and still mount so `App.vue` shows engine error state.

- [ ] **Step 5: Verify GREEN, type checking, and Electron build preservation**

```powershell
npm run test:renderer -- renderer/src/platform/tauri-bridge.test.ts
npm run typecheck
npm run build
```

Expected: bridge tests pass and Electron renderer/main/preload still compile.

- [ ] **Step 6: Commit the shared bridge**

```powershell
git add electron-app/renderer/src/platform electron-app/renderer/src/main.ts electron-app/renderer/src/env.d.ts electron-app/shared/api-types.ts
git commit -m "feat: add Tauri renderer compatibility bridge"
```

---

### Task 7: Gate Unmigrated UI and Connect Native Rename Drops

**Files:**
- Create: `electron-app/renderer/src/platform/runtime.test.ts`
- Create: `electron-app/renderer/src/components/SideNav.test.ts`
- Modify: `electron-app/renderer/src/platform/runtime.ts`
- Modify: `electron-app/renderer/src/App.vue`
- Modify: `electron-app/renderer/src/components/SideNav.vue`
- Modify: `electron-app/renderer/src/components/panels/RenamePanel.vue`

**Interfaces:**
- Consumes: `window.desktopRuntime.capabilities`, `window.electronAPI.onFileDrop`
- Produces: disabled PDF/scan navigation in Tauri, safe saved-panel fallback to Rename, native drop subscription cleanup

- [ ] **Step 1: Write capability and navigation tests**

In `runtime.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { panelIsEnabled, sanitizePanel, tauriPhaseOneCapabilities } from "./runtime";

describe("phase-one capabilities", () => {
  it("enables only migrated functional panels", () => {
    expect(panelIsEnabled("rename", tauriPhaseOneCapabilities)).toBe(true);
    expect(panelIsEnabled("pdf_split", tauriPhaseOneCapabilities)).toBe(false);
    expect(panelIsEnabled("scan_split", tauriPhaseOneCapabilities)).toBe(false);
    expect(panelIsEnabled("about", tauriPhaseOneCapabilities)).toBe(true);
  });

  it("falls back from an unavailable saved panel", () => {
    expect(sanitizePanel("pdf_split", tauriPhaseOneCapabilities)).toBe("rename");
  });
});
```

In `SideNav.test.ts` with `// @vitest-environment happy-dom`:

```typescript
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SideNav from "./SideNav.vue";

describe("SideNav", () => {
  it("disables and does not emit unavailable navigation", async () => {
    const wrapper = mount(SideNav, { props: { active: "rename", disabled: ["pdf_split", "scan_split"] } });
    const pdf = wrapper.get('[data-nav-key="pdf_split"]');
    expect(pdf.attributes("disabled")).toBeDefined();
    await pdf.trigger("click");
    expect(wrapper.emitted("navigate")).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
npm run test:renderer -- renderer/src/platform/runtime.test.ts renderer/src/components/SideNav.test.ts
```

Expected: tests fail because helpers, props, and `data-nav-key` are missing.

- [ ] **Step 3: Implement capability gating**

Export in `runtime.ts`:

```typescript
export type PanelKey = "rename" | "pdf_split" | "scan_split" | "about";

export function panelIsEnabled(panel: PanelKey, caps: DesktopCapabilities): boolean {
  if (panel === "pdf_split") return caps.pdfSplit;
  if (panel === "scan_split") return caps.scanSplit;
  return true;
}

export function sanitizePanel(panel: string | null, caps: DesktopCapabilities): PanelKey {
  const candidate = (["rename", "pdf_split", "scan_split", "about"] as const).find((key) => key === panel);
  return candidate && panelIsEnabled(candidate, caps) ? candidate : "rename";
}
```

Pass disabled keys from `App.vue` to `SideNav`. Add `disabled?: string[]` to SideNav, set native `disabled`, `aria-disabled`, `data-nav-key`, and title `Tauri 第一阶段尚未迁移此功能`. Guard `onNavigate` with `panelIsEnabled` and sanitize session storage during mount.

- [ ] **Step 4: Subscribe RenamePanel to native file drops**

Add a component-level cleanup variable:

```typescript
let unsubscribeNativeDrop: (() => void) | null = null;
```

During mount:

```typescript
unsubscribeNativeDrop = window.electronAPI?.onFileDrop?.((paths) => {
  if (paths.length) appendFiles(paths);
}) ?? null;
```

Call `unsubscribeNativeDrop?.()` in the existing `onBeforeUnmount`. Keep the existing HTML `onDrop` path for Electron; the Tauri native event supplies filesystem paths independently.

- [ ] **Step 5: Verify GREEN and renderer quality**

```powershell
npm run test:renderer
npm run typecheck
npm run build
```

Expected: all renderer tests pass, TypeScript is clean, and Electron build remains successful.

- [ ] **Step 6: Commit phase-one UI gating**

```powershell
git add electron-app/renderer/src/platform electron-app/renderer/src/App.vue electron-app/renderer/src/components/SideNav.vue electron-app/renderer/src/components/SideNav.test.ts electron-app/renderer/src/components/panels/RenamePanel.vue
git commit -m "feat: gate Tauri phase-one capabilities"
```

---

### Task 8: Verify the Real Rename Flow and Document Development Commands

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-19-tauri-phase-one.md` only to check completed boxes during execution

**Interfaces:**
- Consumes: completed Rust commands, Tauri bridge, shared renderer, real `.venv` Python engine
- Produces: verified Windows debug build and reproducible developer instructions

- [x] **Step 1: Run every automated suite from a clean state**

```powershell
Set-Location 'E:\new\file-toolbox\.worktrees\tauri-migration'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
Set-Location electron-app
npm run test:renderer
npm run typecheck
npm run build
& "$env:USERPROFILE\.cargo\bin\cargo.exe" fmt --manifest-path src-tauri/Cargo.toml -- --check
& "$env:USERPROFILE\.cargo\bin\cargo.exe" clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
& "$env:USERPROFILE\.cargo\bin\cargo.exe" test --manifest-path src-tauri/Cargo.toml -- --nocapture
npm run tauri:build:debug
```

Expected: all commands exit 0 without warnings treated as errors.

- [ ] **Step 2: Run the Tauri application and exercise temporary files**

Create a temporary directory outside the repository and three small text files. Run:

```powershell
npm run tauri:dev
```

Verify in the UI:

1. Engine status reaches ready and retry restarts it without creating a second Python process.
2. File dialog adds the temporary files and displays sizes.
3. Native drag-and-drop adds another temporary file exactly once.
4. Rename preview matches the selected rule.
5. Copy mode creates outputs without modifying sources.
6. Overwrite mode performs in-place rename without replacing an unrelated existing target.
7. Undo deletes created copies or restores renamed sources as appropriate.
8. PDF and Scan navigation are disabled with the migration explanation.
9. Closing Tauri leaves no child whose command line contains this worktree's `engine/server.py`.

If any step fails, capture the exact operation and add a failing automated test before changing production code.

- [x] **Step 3: Record the first Tauri size baseline**

```powershell
Get-ChildItem -LiteralPath '.\src-tauri\target\debug' -Filter 'file-toolbox*.exe' |
  Select-Object FullName,Length
```

Record the executable byte size in the README experimental Tauri section without presenting it as the final installer size.

- [x] **Step 4: Add reproducible README commands**

Document:

```powershell
py -3.14 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
Set-Location electron-app
npm ci
npm run tauri:dev
npm run tauri:build:debug
```

Label Tauri as experimental phase one, Windows-only, rename-only, and retain the existing Electron build instructions unchanged.

- [ ] **Step 5: Commit documentation and final verification evidence**

```powershell
git add README.md docs/superpowers/plans/2026-08-19-tauri-phase-one.md
git commit -m "docs: add Tauri phase-one workflow"
git status --short --branch
```

Expected: clean `codex/tauri-migration` worktree with all phase-one commits present.

## Reference Documentation

- Tauri commands and managed state: `https://v2.tauri.app/develop/calling-rust/`
- Tauri events from Rust: `https://v2.tauri.app/develop/calling-frontend/`
- Tauri dialog plugin: `https://v2.tauri.app/plugin/dialog/`
- Tauri webview drag/drop: `https://v2.tauri.app/reference/javascript/api/namespacewebview/`
- Tauri `RunEvent`: `https://docs.rs/tauri/latest/tauri/enum.RunEvent.html`
