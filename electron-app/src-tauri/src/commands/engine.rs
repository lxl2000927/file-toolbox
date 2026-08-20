use crate::commands::files::{validate_rename_params, FileState, PathAuthorizer};
use crate::engine::{
    error::DesktopError,
    manager::{EngineConfig, EngineEventSink, EngineManager, EngineNotification, EngineStatus},
};
use std::{
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager, Runtime, State};

pub const ENGINE_NOTIFICATION_EVENT: &str = "engine-notification";

pub struct AppState {
    pub engine: Arc<EngineManager>,
}

#[derive(Debug, serde::Serialize)]
pub struct EngineStatusResponse {
    pub status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

pub fn is_allowed_method(method: &str) -> bool {
    matches!(
        method,
        "ping" | "rename.preview" | "rename.execute" | "rename.undo"
    )
}

pub fn method_timeout(method: &str) -> Duration {
    if method == "rename.execute" {
        Duration::from_secs(300)
    } else {
        Duration::from_secs(120)
    }
}

fn authorize_rename_outputs(method: &str, result: &serde_json::Value, authorizer: &PathAuthorizer) {
    if method != "rename.execute" {
        return;
    }
    let Some(operations) = result
        .get("operations")
        .and_then(serde_json::Value::as_array)
    else {
        return;
    };
    for operation in operations {
        if operation
            .get("success")
            .and_then(serde_json::Value::as_bool)
            != Some(true)
        {
            continue;
        }
        if let Some(path) = operation
            .get("new_path")
            .and_then(serde_json::Value::as_str)
        {
            let _ = authorizer.authorize_file(path);
        }
    }
}

pub fn development_engine_config(root: &Path, auth_token: impl Into<String>) -> EngineConfig {
    EngineConfig {
        program: root.join(".venv").join("Scripts").join("python.exe"),
        args: vec![root.join("engine").join("server.py").into_os_string()],
        cwd: root.to_path_buf(),
        auth_token: auth_token.into(),
        debug_errors: cfg!(debug_assertions),
    }
}

pub fn packaged_engine_config(resource_dir: &Path, auth_token: impl Into<String>) -> EngineConfig {
    EngineConfig {
        program: resource_dir.join("engine").join("engine.exe"),
        args: Vec::new(),
        cwd: resource_dir.to_path_buf(),
        auth_token: auth_token.into(),
        debug_errors: false,
    }
}

fn development_root(manifest_dir: &Path) -> Result<PathBuf, DesktopError> {
    manifest_dir
        .join("..")
        .join("..")
        .canonicalize()
        .map_err(|error| {
            DesktopError::new(
                "ENGINE_NOT_CONFIGURED",
                format!("无法定位开发仓库根目录: {error}"),
            )
        })
}

pub fn map_engine_status(status: &EngineStatus) -> EngineStatusResponse {
    match status {
        EngineStatus::Starting => EngineStatusResponse {
            status: "starting",
            error: None,
        },
        EngineStatus::Ready => EngineStatusResponse {
            status: "ready",
            error: None,
        },
        EngineStatus::Stopped => EngineStatusResponse {
            status: "error",
            error: Some("引擎已停止".into()),
        },
        EngineStatus::Stopping => EngineStatusResponse {
            status: "error",
            error: Some("引擎正在关闭".into()),
        },
        EngineStatus::Failed(error) => EngineStatusResponse {
            status: "error",
            error: Some(error.clone()),
        },
    }
}

pub struct TauriEventSink<R: Runtime> {
    app: AppHandle<R>,
}

impl<R: Runtime> TauriEventSink<R> {
    pub fn new(app: AppHandle<R>) -> Self {
        Self { app }
    }
}

impl<R: Runtime> EngineEventSink for TauriEventSink<R> {
    fn emit(&self, notification: EngineNotification) {
        let _ = self.app.emit(ENGINE_NOTIFICATION_EVENT, notification);
    }
}

fn engine_status_notification<R: Runtime>(app: &AppHandle<R>, status: &EngineStatus) {
    let mapped = map_engine_status(status);
    let params = serde_json::to_value(&mapped)
        .unwrap_or_else(|_| serde_json::json!({"status": "error", "error": "无法编码引擎状态"}));
    let _ = app.emit(
        ENGINE_NOTIFICATION_EVENT,
        EngineNotification {
            method: "engine.status".into(),
            params,
        },
    );
}

pub async fn emit_status<R: Runtime>(app: &AppHandle<R>, engine: &EngineManager) {
    engine_status_notification(app, &engine.status().await);
}

pub async fn engine_status(state: State<'_, AppState>) -> EngineStatusResponse {
    map_engine_status(&state.engine.status().await)
}

#[tauri::command(rename = "engine_status")]
pub async fn engine_status_command(
    state: State<'_, AppState>,
) -> Result<EngineStatusResponse, DesktopError> {
    Ok(engine_status(state).await)
}

#[tauri::command]
pub async fn engine_call(
    method: String,
    params: serde_json::Value,
    state: State<'_, AppState>,
    files: State<'_, FileState>,
) -> Result<serde_json::Value, DesktopError> {
    if !is_allowed_method(&method) {
        return Err(DesktopError::new("METHOD_NOT_ALLOWED", "引擎方法未获允许"));
    }
    validate_rename_params(&method, &params, &files.paths)?;
    let result = state
        .engine
        .call(&method, params, method_timeout(&method))
        .await?;
    authorize_rename_outputs(&method, &result, &files.paths);
    Ok(result)
}

#[tauri::command]
pub async fn engine_restart(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<(), DesktopError> {
    let result = state.engine.restart().await;
    emit_status(&app, &state.engine).await;
    result
}

pub fn engine_config<R: Runtime>(
    app: &AppHandle<R>,
    auth_token: String,
) -> Result<EngineConfig, DesktopError> {
    if cfg!(debug_assertions) {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let root = development_root(&manifest_dir)?;
        Ok(development_engine_config(&root, auth_token))
    } else {
        let resource_dir = app.path().resource_dir().map_err(|error| {
            DesktopError::new(
                "ENGINE_NOT_CONFIGURED",
                format!("无法定位打包引擎资源: {error}"),
            )
        })?;
        Ok(packaged_engine_config(&resource_dir, auth_token))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{path::PathBuf, time::Duration};

    #[test]
    fn development_root_is_canonicalized() {
        let temp = tempfile::tempdir().unwrap();
        let manifest_dir = temp.path().join("electron-app").join("src-tauri");
        std::fs::create_dir_all(&manifest_dir).unwrap();

        let root = development_root(&manifest_dir).unwrap();

        assert_eq!(root, temp.path().canonicalize().unwrap());
        assert!(!root
            .components()
            .any(|component| matches!(component, std::path::Component::ParentDir)));
    }

    #[test]
    fn phase_one_allowlist_is_exact() {
        for method in ["ping", "rename.preview", "rename.execute", "rename.undo"] {
            assert!(is_allowed_method(method));
        }
        for method in [
            "shutdown",
            "pdf_split.preview",
            "scan_split.execute_async",
            "history.clear",
        ] {
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
        assert_eq!(
            config.args,
            vec![root.join(r"engine\server.py").into_os_string()]
        );
    }

    #[test]
    fn packaged_config_uses_resource_engine() {
        let resource_dir = PathBuf::from(r"C:\Program Files\File Toolbox\resources");
        let config = packaged_engine_config(&resource_dir, "token");
        assert_eq!(config.program, resource_dir.join(r"engine\engine.exe"));
        assert!(config.args.is_empty());
        assert_eq!(config.cwd, resource_dir);
        assert!(!config.debug_errors);
    }

    #[test]
    fn rename_execute_authorizes_successful_output_paths() {
        let temp = tempfile::tempdir().unwrap();
        let renamed = temp.path().join("renamed.txt");
        std::fs::write(&renamed, b"renamed").unwrap();
        let authorizer = crate::commands::files::PathAuthorizer::default();
        let result = serde_json::json!({
            "operations": [{
                "success": true,
                "new_path": renamed,
            }]
        });

        assert!(!authorizer.is_authorized(&renamed));
        authorize_rename_outputs("rename.execute", &result, &authorizer);
        assert!(authorizer.is_authorized(&renamed));
    }

    #[test]
    fn internal_states_map_to_frontend_statuses() {
        let starting = map_engine_status(&EngineStatus::Starting);
        assert_eq!(starting.status, "starting");
        assert_eq!(starting.error, None);

        let ready = map_engine_status(&EngineStatus::Ready);
        assert_eq!(ready.status, "ready");
        assert_eq!(ready.error, None);

        for (state, expected_error) in [
            (EngineStatus::Stopped, "引擎已停止"),
            (EngineStatus::Stopping, "引擎正在关闭"),
            (EngineStatus::Failed("boom".into()), "boom"),
        ] {
            let response = map_engine_status(&state);
            assert_eq!(response.status, "error");
            assert_eq!(response.error.as_deref(), Some(expected_error));
        }
    }
}
