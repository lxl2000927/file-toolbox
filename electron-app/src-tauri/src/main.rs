// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod engine;

use commands::engine::{
    engine_call, engine_config, engine_restart, engine_status_command, AppState, TauriEventSink,
};
use std::sync::{
    atomic::{AtomicU8, Ordering},
    Arc,
};
use tauri::{async_runtime, Manager, RunEvent};
use uuid::Uuid;

fn main() {
    const SHUTDOWN_IDLE: u8 = 0;
    const SHUTDOWN_IN_PROGRESS: u8 = 1;
    const SHUTDOWN_COMPLETE: u8 = 2;

    let shutdown_state = Arc::new(AtomicU8::new(SHUTDOWN_IDLE));
    tauri::Builder::default()
        .setup(|app| {
            let auth_token = Uuid::new_v4().simple().to_string();
            let config =
                engine_config(app.handle(), auth_token).map_err(|error| error.to_string())?;
            let manager = Arc::new(engine::manager::EngineManager::new(
                config,
                Arc::new(TauriEventSink::new(app.handle().clone())),
            ));
            app.manage(AppState {
                engine: Arc::clone(&manager),
            });
            let startup_manager = Arc::clone(&manager);
            let startup_app = app.handle().clone();
            async_runtime::spawn(async move {
                let _ = startup_manager.start().await;
                commands::engine::emit_status(&startup_app, &startup_manager).await;
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            engine_status_command,
            engine_call,
            engine_restart
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |app, event| {
            if let RunEvent::ExitRequested { api, code, .. } = event {
                if shutdown_state.load(Ordering::Acquire) == SHUTDOWN_COMPLETE {
                    return;
                }
                api.prevent_exit();
                if shutdown_state
                    .compare_exchange(
                        SHUTDOWN_IDLE,
                        SHUTDOWN_IN_PROGRESS,
                        Ordering::AcqRel,
                        Ordering::Acquire,
                    )
                    .is_err()
                {
                    return;
                }
                let engine = app.state::<AppState>().engine.clone();
                let app = app.clone();
                let shutdown_state = Arc::clone(&shutdown_state);
                async_runtime::spawn(async move {
                    let _ = engine.shutdown().await;
                    shutdown_state.store(SHUTDOWN_COMPLETE, Ordering::Release);
                    app.exit(code.unwrap_or(0));
                });
            }
        })
}
