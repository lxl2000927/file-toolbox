use super::{
    error::DesktopError,
    protocol::{encode_request, parse_line, IncomingMessage},
};
use std::{
    collections::HashMap,
    ffi::OsString,
    path::PathBuf,
    process::Stdio,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStderr, ChildStdin, ChildStdout},
    sync::{oneshot, Mutex},
};

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
#[serde(tag = "status", content = "error", rename_all = "lowercase")]
pub enum EngineStatus {
    Stopped,
    Starting,
    Ready,
    Failed(String),
    Stopping,
}

#[derive(Debug, Clone)]
pub struct EngineConfig {
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub cwd: PathBuf,
    pub auth_token: String,
    pub debug_errors: bool,
}

#[derive(Debug, Clone, serde::Serialize, PartialEq)]
pub struct EngineNotification {
    pub method: String,
    pub params: serde_json::Value,
}

pub trait EngineEventSink: Send + Sync + 'static {
    fn emit(&self, notification: EngineNotification);
}

type PendingSender = oneshot::Sender<Result<serde_json::Value, DesktopError>>;

#[derive(Clone)]
pub struct EngineManager {
    config: EngineConfig,
    sink: Arc<dyn EngineEventSink>,
    state: Arc<Mutex<EngineStatus>>,
    lifecycle: Arc<Mutex<()>>,
    child: Arc<Mutex<Option<Child>>>,
    stdin: Arc<Mutex<Option<ChildStdin>>>,
    pending: Arc<Mutex<HashMap<u64, PendingSender>>>,
    next_id: Arc<AtomicU64>,
}

impl EngineManager {
    pub fn new(config: EngineConfig, sink: Arc<dyn EngineEventSink>) -> Self {
        Self {
            config,
            sink,
            state: Arc::new(Mutex::new(EngineStatus::Stopped)),
            lifecycle: Arc::new(Mutex::new(())),
            child: Arc::new(Mutex::new(None)),
            stdin: Arc::new(Mutex::new(None)),
            pending: Arc::new(Mutex::new(HashMap::new())),
            next_id: Arc::new(AtomicU64::new(1)),
        }
    }

    pub async fn status(&self) -> EngineStatus {
        self.state.lock().await.clone()
    }

    pub async fn start(&self) -> Result<(), DesktopError> {
        let _lifecycle = self.lifecycle.lock().await;
        self.start_locked().await
    }

    async fn start_locked(&self) -> Result<(), DesktopError> {
        if matches!(self.status().await, EngineStatus::Ready) {
            return Ok(());
        }
        if self.child.lock().await.is_some() {
            self.stop_child().await;
        }

        {
            let mut state = self.state.lock().await;
            if matches!(*state, EngineStatus::Starting) {
                return Err(DesktopError::new("ENGINE_START_FAILED", "引擎正在启动"));
            }
            *state = EngineStatus::Starting;
        }

        let mut command = build_command(&self.config);
        command.kill_on_drop(true);
        let spawn_result = command.spawn();
        let mut child = match spawn_result {
            Ok(child) => child,
            Err(error) => {
                *self.state.lock().await = EngineStatus::Failed(error.to_string());
                return Err(DesktopError::new(
                    "ENGINE_START_FAILED",
                    format!("无法启动引擎: {error}"),
                ));
            }
        };

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| DesktopError::new("ENGINE_START_FAILED", "引擎 stdout 未连接"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| DesktopError::new("ENGINE_START_FAILED", "引擎 stderr 未连接"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| DesktopError::new("ENGINE_START_FAILED", "引擎 stdin 未连接"))?;
        let process_id = child.id();

        *self.child.lock().await = Some(child);
        *self.stdin.lock().await = Some(stdin);

        let (ready_sender, ready_receiver) = oneshot::channel();
        spawn_stdout_reader(
            Arc::downgrade(&self.state),
            Arc::downgrade(&self.child),
            Arc::downgrade(&self.pending),
            Arc::downgrade(&self.stdin),
            ready_sender,
            Arc::clone(&self.sink),
            stdout,
            process_id,
        );
        spawn_stderr_logger(stderr);
        spawn_child_watcher(
            Arc::downgrade(&self.state),
            Arc::downgrade(&self.child),
            Arc::downgrade(&self.pending),
            Arc::downgrade(&self.stdin),
            Arc::clone(&self.sink),
            process_id,
        );

        let ready_result = tokio::time::timeout(Duration::from_secs(30), ready_receiver).await;

        match ready_result {
            Ok(Ok(())) => {
                let mut state = self.state.lock().await;
                if matches!(*state, EngineStatus::Starting) {
                    *state = EngineStatus::Ready;
                    Ok(())
                } else {
                    Err(DesktopError::new("ENGINE_EXITED", "引擎在启动期间退出"))
                }
            }
            Ok(Err(_)) => {
                self.stop_child().await;
                *self.state.lock().await = EngineStatus::Failed("引擎在启动期间退出".into());
                Err(DesktopError::new("ENGINE_EXITED", "引擎在启动期间退出"))
            }
            Err(_) => {
                let _ = self.stop_child().await;
                *self.state.lock().await = EngineStatus::Failed("引擎启动超时".into());
                Err(DesktopError::new("ENGINE_TIMEOUT", "等待引擎就绪超时"))
            }
        }
    }

    pub async fn call(
        &self,
        method: &str,
        params: serde_json::Value,
        timeout: Duration,
    ) -> Result<serde_json::Value, DesktopError> {
        if !matches!(self.status().await, EngineStatus::Ready) {
            return Err(DesktopError::new("ENGINE_NOT_READY", "引擎尚未就绪"));
        }
        self.call_request(method, params, timeout).await
    }

    async fn call_request(
        &self,
        method: &str,
        params: serde_json::Value,
        timeout: Duration,
    ) -> Result<serde_json::Value, DesktopError> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let request = encode_request(id, method, params, &self.config.auth_token)?;
        let (sender, receiver) = oneshot::channel();
        self.pending.lock().await.insert(id, sender);

        let write_result = {
            let mut stdin = self.stdin.lock().await;
            match stdin.as_mut() {
                Some(stdin) => match stdin.write_all(request.as_bytes()).await {
                    Ok(()) => stdin.write_all(b"\n").await,
                    Err(error) => Err(error),
                },
                None => Err(std::io::Error::new(
                    std::io::ErrorKind::BrokenPipe,
                    "engine stdin is closed",
                )),
            }
        };

        if let Err(error) = write_result {
            self.pending.lock().await.remove(&id);
            return Err(DesktopError::new(
                "ENGINE_EXITED",
                format!("引擎 stdin 写入失败: {error}"),
            ));
        }

        match tokio::time::timeout(timeout, receiver).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err(DesktopError::new("ENGINE_EXITED", "引擎进程已退出")),
            Err(_) => {
                self.pending.lock().await.remove(&id);
                Err(DesktopError::new(
                    "ENGINE_TIMEOUT",
                    format!("引擎请求超时: {method}"),
                ))
            }
        }
    }

    pub async fn restart(&self) -> Result<(), DesktopError> {
        let _lifecycle = self.lifecycle.lock().await;
        let _ = self.shutdown_locked().await;
        self.start_locked().await
    }

    pub async fn shutdown(&self) -> Result<(), DesktopError> {
        let _lifecycle = self.lifecycle.lock().await;
        self.shutdown_locked().await
    }

    async fn shutdown_locked(&self) -> Result<(), DesktopError> {
        if self.child.lock().await.is_none() {
            *self.state.lock().await = EngineStatus::Stopped;
            return Ok(());
        }

        *self.state.lock().await = EngineStatus::Stopping;

        let _shutdown_result = self
            .call_request("shutdown", serde_json::json!({}), Duration::from_secs(2))
            .await;
        let wait_result = tokio::time::timeout(Duration::from_millis(1500), async {
            loop {
                if self.child_has_exited().await {
                    return;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await;

        if wait_result.is_err() {
            let mut child = self.child.lock().await;
            if let Some(child) = child.as_mut() {
                let _ = child.kill().await;
            }
        }
        self.wait_child().await;

        self.stdin.lock().await.take();
        reject_pending(
            &self.pending,
            DesktopError::new("ENGINE_EXITED", "引擎进程已退出"),
        )
        .await;
        *self.state.lock().await = EngineStatus::Stopped;

        Ok(())
    }

    async fn child_has_exited(&self) -> bool {
        let mut child = self.child.lock().await;
        match child.as_mut() {
            Some(child) => matches!(child.try_wait(), Ok(Some(_)) | Err(_)),
            None => true,
        }
    }

    async fn wait_child(&self) {
        let child = self.child.lock().await.take();
        if let Some(mut child) = child {
            let _ = child.wait().await;
        }
    }

    async fn stop_child(&self) {
        {
            let mut child = self.child.lock().await;
            if let Some(child) = child.as_mut() {
                let _ = child.kill().await;
            }
        }
        self.wait_child().await;
        self.stdin.lock().await.take();
        reject_pending(
            &self.pending,
            DesktopError::new("ENGINE_EXITED", "引擎进程已退出"),
        )
        .await;
    }
}

fn build_command(config: &EngineConfig) -> tokio::process::Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;

        let mut command = std::process::Command::new(&config.program);
        command
            .args(&config.args)
            .current_dir(&config.cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("FILE_TOOLBOX_ENGINE_TOKEN", &config.auth_token)
            .env(
                "FILE_TOOLBOX_ENGINE_DEBUG_ERRORS",
                if config.debug_errors { "1" } else { "0" },
            )
            .creation_flags(CREATE_NO_WINDOW);
        tokio::process::Command::from(command)
    }

    #[cfg(not(windows))]
    {
        let mut command = tokio::process::Command::new(&config.program);
        command
            .args(&config.args)
            .current_dir(&config.cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("FILE_TOOLBOX_ENGINE_TOKEN", &config.auth_token)
            .env(
                "FILE_TOOLBOX_ENGINE_DEBUG_ERRORS",
                if config.debug_errors { "1" } else { "0" },
            );
        command
    }
}

#[allow(clippy::too_many_arguments)]
fn spawn_stdout_reader(
    state: std::sync::Weak<Mutex<EngineStatus>>,
    child: std::sync::Weak<Mutex<Option<Child>>>,
    pending: std::sync::Weak<Mutex<HashMap<u64, PendingSender>>>,
    stdin: std::sync::Weak<Mutex<Option<ChildStdin>>>,
    ready: oneshot::Sender<()>,
    sink: Arc<dyn EngineEventSink>,
    stdout: ChildStdout,
    process_id: Option<u32>,
) {
    tokio::spawn(async move {
        let mut ready = Some(ready);
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            match parse_line(&line) {
                Ok(IncomingMessage::Ready) => {
                    if let Some(ready) = ready.take() {
                        let _ = ready.send(());
                    }
                }
                Ok(IncomingMessage::Response { id, result }) => {
                    if let Some(pending) = pending.upgrade() {
                        if let Some(sender) = pending.lock().await.remove(&id) {
                            let _ = sender.send(result);
                        }
                    }
                }
                Ok(IncomingMessage::Notification { method, params }) => {
                    sink.emit(EngineNotification { method, params });
                }
                Err(error) => {
                    eprintln!("engine protocol error: {error}");
                }
            }
        }

        handle_process_exit(state, child, pending, stdin, sink, process_id).await;
    });
}

fn spawn_stderr_logger(stderr: ChildStderr) {
    tokio::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            eprintln!("engine stderr: {line}");
        }
    });
}

fn spawn_child_watcher(
    state: std::sync::Weak<Mutex<EngineStatus>>,
    child: std::sync::Weak<Mutex<Option<Child>>>,
    pending: std::sync::Weak<Mutex<HashMap<u64, PendingSender>>>,
    stdin: std::sync::Weak<Mutex<Option<ChildStdin>>>,
    sink: Arc<dyn EngineEventSink>,
    process_id: Option<u32>,
) {
    tokio::spawn(async move {
        loop {
            let exited = match child.upgrade() {
                Some(child) => {
                    let mut child_guard = child.lock().await;
                    match child_guard.as_mut() {
                        Some(child) if child.id() == process_id => match child.try_wait() {
                            Ok(Some(_)) => true,
                            Ok(None) => false,
                            Err(_) => true,
                        },
                        _ => return,
                    }
                }
                None => return,
            };

            if exited {
                handle_process_exit(state, child, pending, stdin, sink, process_id).await;
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    });
}

async fn handle_process_exit(
    state: std::sync::Weak<Mutex<EngineStatus>>,
    child: std::sync::Weak<Mutex<Option<Child>>>,
    pending: std::sync::Weak<Mutex<HashMap<u64, PendingSender>>>,
    stdin: std::sync::Weak<Mutex<Option<ChildStdin>>>,
    sink: Arc<dyn EngineEventSink>,
    process_id: Option<u32>,
) {
    let Some(child) = child.upgrade() else {
        return;
    };
    let is_current = {
        let child_guard = child.lock().await;
        child_guard
            .as_ref()
            .is_some_and(|child| child.id() == process_id)
    };
    if !is_current {
        return;
    }

    if let Some(stdin) = stdin.upgrade() {
        stdin.lock().await.take();
    }
    if let Some(pending) = pending.upgrade() {
        reject_pending(
            &pending,
            DesktopError::new("ENGINE_EXITED", "引擎进程已退出"),
        )
        .await;
    }

    let mut status_changed = false;
    if let Some(state) = state.upgrade() {
        let mut state = state.lock().await;
        status_changed = mark_engine_failed(&mut state);
    }
    if status_changed {
        sink.emit(EngineNotification {
            method: "engine.status".into(),
            params: serde_json::json!({
                "status": "error",
                "error": "引擎进程已退出",
            }),
        });
    }
}

fn mark_engine_failed(state: &mut EngineStatus) -> bool {
    if matches!(*state, EngineStatus::Starting | EngineStatus::Ready) {
        *state = EngineStatus::Failed("引擎进程已退出".into());
        true
    } else {
        false
    }
}

async fn reject_pending(pending: &Arc<Mutex<HashMap<u64, PendingSender>>>, error: DesktopError) {
    let mut pending = pending.lock().await;
    let senders = pending
        .drain()
        .map(|(_, sender)| sender)
        .collect::<Vec<_>>();
    drop(pending);
    for sender in senders {
        let _ = sender.send(Err(error.clone()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{
        path::PathBuf,
        sync::{Arc, Mutex},
        time::Duration,
    };

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
            args: vec![manifest
                .join("tests/fixtures/fake_engine.py")
                .into_os_string()],
            cwd: manifest.join("../.."),
            auth_token: "test-token".into(),
            debug_errors: true,
        }
    }

    async fn wait_for_failed(manager: &EngineManager) {
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if matches!(manager.status().await, EngineStatus::Failed(_)) {
                    return;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("engine did not enter the failed state");
    }

    #[tokio::test]
    async fn starts_calls_and_shuts_down() {
        let manager = EngineManager::new(fixture_config(), Arc::new(RecordingSink::default()));
        manager.start().await.unwrap();
        assert_eq!(manager.status().await, EngineStatus::Ready);
        assert_eq!(
            manager
                .call("ping", json!({}), Duration::from_secs(2))
                .await
                .unwrap(),
            json!({"pong": true})
        );
        manager.shutdown().await.unwrap();
        assert_eq!(manager.status().await, EngineStatus::Stopped);
        assert!(manager.child.lock().await.is_none());
    }

    #[tokio::test]
    async fn forwards_notifications_and_times_out_requests() {
        let sink = Arc::new(RecordingSink::default());
        let manager = EngineManager::new(fixture_config(), sink.clone());
        manager.start().await.unwrap();
        manager
            .call("emit_test", json!({}), Duration::from_secs(2))
            .await
            .unwrap();
        assert_eq!(sink.0.lock().unwrap()[0].method, "task.progress");
        let error = manager
            .call("hang", json!({}), Duration::from_millis(50))
            .await
            .unwrap_err();
        assert_eq!(error.code, "ENGINE_TIMEOUT");
        assert!(manager.pending.lock().await.is_empty());
        manager.shutdown().await.unwrap();
    }

    #[tokio::test]
    async fn crash_marks_engine_failed_and_restart_recovers() {
        let sink = Arc::new(RecordingSink::default());
        let manager = EngineManager::new(fixture_config(), sink.clone());
        manager.start().await.unwrap();
        let error = manager
            .call("crash", json!({}), Duration::from_secs(2))
            .await
            .unwrap_err();
        assert_eq!(error.code, "ENGINE_EXITED");
        wait_for_failed(&manager).await;
        let failure_notifications = sink
            .0
            .lock()
            .unwrap()
            .iter()
            .filter(|notification| {
                notification.method == "engine.status"
                    && notification.params == json!({"status": "error", "error": "引擎进程已退出"})
            })
            .count();
        assert_eq!(failure_notifications, 1);
        manager.restart().await.unwrap();
        assert_eq!(
            manager
                .call("ping", json!({}), Duration::from_secs(2))
                .await
                .unwrap(),
            json!({"pong": true})
        );
        manager.shutdown().await.unwrap();
    }

    #[test]
    fn failure_transition_records_exactly_one_status_event() {
        let mut state = EngineStatus::Ready;
        let mut failure_events = 0;
        if mark_engine_failed(&mut state) {
            failure_events += 1;
        }
        if mark_engine_failed(&mut state) {
            failure_events += 1;
        }
        assert_eq!(state, EngineStatus::Failed("引擎进程已退出".into()));
        assert_eq!(failure_events, 1);
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn dropping_last_manager_owner_terminates_the_child() {
        let manager = EngineManager::new(fixture_config(), Arc::new(RecordingSink::default()));
        manager.start().await.unwrap();
        let process_id = manager.child.lock().await.as_ref().unwrap().id().unwrap();

        drop(manager);

        wait_for_process_exit(process_id).await;
    }

    #[cfg(windows)]
    async fn wait_for_process_exit(process_id: u32) {
        tokio::time::timeout(Duration::from_secs(2), async move {
            while process_is_running(process_id) {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("engine process survived after its last manager owner was dropped");
    }

    #[cfg(windows)]
    fn process_is_running(process_id: u32) -> bool {
        const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
        const STILL_ACTIVE: u32 = 259;

        #[link(name = "kernel32")]
        extern "system" {
            fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
            fn GetExitCodeProcess(handle: *mut std::ffi::c_void, exit_code: *mut u32) -> i32;
            fn OpenProcess(
                access: u32,
                inherit_handle: i32,
                process_id: u32,
            ) -> *mut std::ffi::c_void;
        }

        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, process_id);
            if handle.is_null() {
                return false;
            }
            let mut exit_code = 0;
            let success = GetExitCodeProcess(handle, &mut exit_code);
            CloseHandle(handle);
            success != 0 && exit_code == STILL_ACTIVE
        }
    }
}
