use crate::engine::error::DesktopError;
use std::{
    collections::VecDeque,
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use tauri_plugin_dialog::DialogExt;

pub const MAX_AUTHORIZED_PATHS: usize = 12_000;
pub const MAX_GENERIC_INPUT_FILE_SIZE: u64 = 500 * 1024 * 1024;

#[derive(Clone)]
enum AuthorizedPathKind {
    File,
    Directory,
}

#[derive(Clone)]
struct AuthorizedPath {
    path: PathBuf,
    kind: AuthorizedPathKind,
}

#[derive(Default)]
pub struct PathAuthorizer {
    entries: Mutex<VecDeque<AuthorizedPath>>,
}

pub struct FileState {
    pub paths: Arc<PathAuthorizer>,
}

impl PathAuthorizer {
    pub fn authorize_file(&self, path: impl AsRef<Path>) -> Result<PathBuf, DesktopError> {
        self.authorize(path.as_ref(), AuthorizedPathKind::File)
    }

    pub fn authorize_directory(&self, path: impl AsRef<Path>) -> Result<PathBuf, DesktopError> {
        self.authorize(path.as_ref(), AuthorizedPathKind::Directory)
    }

    pub fn is_authorized(&self, path: impl AsRef<Path>) -> bool {
        let Ok(actual) = canonicalize_existing(path.as_ref()) else {
            return false;
        };
        let entries = self
            .entries
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        entries.iter().any(|entry| match entry.kind {
            AuthorizedPathKind::File => paths_equal(&entry.path, &actual),
            AuthorizedPathKind::Directory => path_is_within(&entry.path, &actual),
        })
    }

    fn authorize(&self, path: &Path, kind: AuthorizedPathKind) -> Result<PathBuf, DesktopError> {
        let canonical = canonicalize_existing(path)?;
        let metadata =
            fs::metadata(&canonical).map_err(|error| path_io_error(&canonical, error))?;
        let kind_matches = match kind {
            AuthorizedPathKind::File => metadata.is_file(),
            AuthorizedPathKind::Directory => metadata.is_dir(),
        };
        if !kind_matches {
            return Err(DesktopError::new(
                "PATH_NOT_AUTHORIZED",
                match kind {
                    AuthorizedPathKind::File => "路径不是文件",
                    AuthorizedPathKind::Directory => "路径不是目录",
                },
            ));
        }

        let mut entries = self
            .entries
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        entries.retain(|entry| !paths_equal(&entry.path, &canonical));
        entries.push_back(AuthorizedPath {
            path: canonical.clone(),
            kind,
        });
        while entries.len() > MAX_AUTHORIZED_PATHS {
            entries.pop_front();
        }
        Ok(canonical)
    }
}

fn canonicalize_existing(path: &Path) -> Result<PathBuf, DesktopError> {
    fs::canonicalize(path).map_err(|error| path_io_error(path, error))
}

fn path_io_error(path: &Path, error: std::io::Error) -> DesktopError {
    let code = match error.kind() {
        std::io::ErrorKind::NotFound => "PATH_NOT_FOUND",
        _ => "PATH_NOT_AUTHORIZED",
    };
    DesktopError::new(code, format!("无法访问路径 {}", path.to_string_lossy()))
}

fn comparison_path(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        PathBuf::from(path.to_string_lossy().to_lowercase())
    }
    #[cfg(not(windows))]
    {
        path.to_path_buf()
    }
}

fn paths_equal(left: &Path, right: &Path) -> bool {
    #[cfg(windows)]
    {
        windows_paths_equal(left, right)
    }
    #[cfg(not(windows))]
    {
        comparison_path(left) == comparison_path(right)
    }
}

fn path_is_within(root: &Path, candidate: &Path) -> bool {
    #[cfg(windows)]
    {
        windows_path_is_within(root, candidate)
    }
    #[cfg(not(windows))]
    {
        candidate.strip_prefix(root).is_ok()
    }
}

#[cfg(windows)]
fn windows_paths_equal(left: &Path, right: &Path) -> bool {
    comparison_path(left) == comparison_path(right)
}

#[cfg(windows)]
fn windows_path_is_within(root: &Path, candidate: &Path) -> bool {
    let root = comparison_path(root);
    let candidate = comparison_path(candidate);
    candidate.strip_prefix(&root).is_ok()
}

pub fn validate_rename_params(
    method: &str,
    params: &serde_json::Value,
    authorizer: &PathAuthorizer,
) -> Result<(), DesktopError> {
    if !matches!(method, "rename.preview" | "rename.execute") {
        return Ok(());
    }

    let files = params
        .get("files")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| DesktopError::new("PATH_NOT_AUTHORIZED", "文件列表格式无效"))?;
    if files.is_empty() {
        return Err(DesktopError::new("PATH_NOT_AUTHORIZED", "文件列表不能为空"));
    }

    for value in files {
        let path = value
            .as_str()
            .filter(|path| !path.trim().is_empty())
            .ok_or_else(|| DesktopError::new("PATH_NOT_AUTHORIZED", "文件路径格式无效"))?;
        validate_authorized_file(Path::new(path), authorizer)?;
    }

    if let Some(output_dir) = params.get("output_dir").and_then(serde_json::Value::as_str) {
        if !output_dir.trim().is_empty() {
            validate_authorized_directory(Path::new(output_dir), authorizer)?;
        }
    } else if params.get("output_dir").is_some() {
        return Err(DesktopError::new("PATH_NOT_AUTHORIZED", "输出目录格式无效"));
    }
    Ok(())
}

fn validate_authorized_file(
    path: &Path,
    authorizer: &PathAuthorizer,
) -> Result<PathBuf, DesktopError> {
    if !authorizer.is_authorized(path) {
        return Err(DesktopError::new("PATH_NOT_AUTHORIZED", "文件路径尚未授权"));
    }
    let canonical = canonicalize_existing(path)?;
    let metadata = fs::metadata(&canonical).map_err(|error| path_io_error(&canonical, error))?;
    if !metadata.is_file() || metadata.len() > MAX_GENERIC_INPUT_FILE_SIZE {
        return Err(DesktopError::new(
            "PATH_NOT_AUTHORIZED",
            "文件不是可处理的常规文件",
        ));
    }
    Ok(canonical)
}

fn validate_authorized_directory(
    path: &Path,
    authorizer: &PathAuthorizer,
) -> Result<PathBuf, DesktopError> {
    if !authorizer.is_authorized(path) {
        return Err(DesktopError::new("PATH_NOT_AUTHORIZED", "输出目录尚未授权"));
    }
    let canonical = canonicalize_existing(path)?;
    let metadata = fs::metadata(&canonical).map_err(|error| path_io_error(&canonical, error))?;
    if !metadata.is_dir() {
        return Err(DesktopError::new("PATH_NOT_AUTHORIZED", "输出路径不是目录"));
    }
    Ok(canonical)
}

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

fn file_access_error(
    code: &'static str,
    message: impl Into<String>,
    path: Option<String>,
) -> FileAccessError {
    FileAccessError {
        code,
        message: message.into(),
        path,
    }
}

fn stat_path(path: &str, authorizer: &PathAuthorizer) -> FileAccessResult<FilePathStat> {
    let path_ref = Path::new(path);
    if path.trim().is_empty() {
        return FileAccessResult::Failure {
            ok: false,
            error: file_access_error("invalid_argument", "路径不能为空", Some(path.into())),
        };
    }
    if !authorizer.is_authorized(path_ref) {
        return FileAccessResult::Failure {
            ok: false,
            error: file_access_error("unauthorized", "路径尚未授权", Some(path.into())),
        };
    }
    match fs::metadata(path_ref) {
        Ok(metadata) => FileAccessResult::Success {
            ok: true,
            value: FilePathStat {
                path: path.into(),
                is_file: metadata.is_file(),
                is_directory: metadata.is_dir(),
                size: metadata.len(),
            },
        },
        Err(error) => {
            let (code, message) = match error.kind() {
                std::io::ErrorKind::NotFound => ("not_found", "路径不存在"),
                std::io::ErrorKind::PermissionDenied => ("permission_denied", "没有权限访问该路径"),
                _ => ("io_error", "文件访问失败"),
            };
            FileAccessResult::Failure {
                ok: false,
                error: file_access_error(code, message, Some(path.into())),
            }
        }
    }
}

#[tauri::command]
pub async fn open_files(
    app: tauri::AppHandle,
    options: Option<OpenFilesOptions>,
    state: tauri::State<'_, FileState>,
) -> Result<Vec<String>, DesktopError> {
    let selected = tauri::async_runtime::spawn_blocking(move || {
        let options = options.unwrap_or(OpenFilesOptions {
            filters: None,
            multi: None,
            title: None,
        });
        let mut dialog = app.dialog().file();
        if let Some(title) = options.title {
            dialog = dialog.set_title(title);
        }
        if let Some(filters) = options.filters {
            for filter in filters {
                let extensions = filter
                    .extensions
                    .iter()
                    .map(String::as_str)
                    .collect::<Vec<_>>();
                dialog = dialog.add_filter(filter.name, &extensions);
            }
        }
        if options.multi.unwrap_or(true) {
            dialog.blocking_pick_files().unwrap_or_default()
        } else {
            dialog.blocking_pick_file().into_iter().collect::<Vec<_>>()
        }
    })
    .await
    .map_err(|error| {
        DesktopError::new("PATH_NOT_AUTHORIZED", format!("文件选择器失败: {error}"))
    })?;

    selected
        .into_iter()
        .map(|file| {
            let path = file.into_path().map_err(|error| {
                DesktopError::new("PATH_NOT_FOUND", format!("无法转换选择的路径: {error}"))
            })?;
            let authorized = state.paths.authorize_file(path)?;
            Ok(authorized.to_string_lossy().into_owned())
        })
        .collect()
}

#[tauri::command]
pub async fn open_directory(
    app: tauri::AppHandle,
    title: Option<String>,
    state: tauri::State<'_, FileState>,
) -> Result<String, DesktopError> {
    let selected = tauri::async_runtime::spawn_blocking(move || {
        let mut dialog = app.dialog().file();
        if let Some(title) = title {
            dialog = dialog.set_title(title);
        }
        dialog.blocking_pick_folder()
    })
    .await
    .map_err(|error| {
        DesktopError::new("PATH_NOT_AUTHORIZED", format!("目录选择器失败: {error}"))
    })?;

    let Some(selected) = selected else {
        return Ok(String::new());
    };
    let path = selected.into_path().map_err(|error| {
        DesktopError::new("PATH_NOT_FOUND", format!("无法转换选择的路径: {error}"))
    })?;
    let authorized = state.paths.authorize_directory(path)?;
    Ok(authorized.to_string_lossy().into_owned())
}

pub async fn stat_paths(
    paths: Vec<String>,
    state: tauri::State<'_, FileState>,
) -> Vec<FileAccessResult<FilePathStat>> {
    paths
        .iter()
        .map(|path| stat_path(path, &state.paths))
        .collect()
}

#[tauri::command(rename = "stat_paths")]
pub async fn stat_paths_command(
    paths: Vec<String>,
    state: tauri::State<'_, FileState>,
) -> Result<Vec<FileAccessResult<FilePathStat>>, DesktopError> {
    Ok(stat_paths(paths, state).await)
}

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
    fn directory_authorization_does_not_match_shared_string_prefixes() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("root");
        let sibling = temp.path().join("root-sibling");
        std::fs::create_dir(&root).unwrap();
        std::fs::create_dir(&sibling).unwrap();
        let outside = sibling.join("file.txt");
        std::fs::write(&outside, b"no").unwrap();

        let auth = PathAuthorizer::default();
        auth.authorize_directory(&root).unwrap();

        assert!(!auth.is_authorized(&outside));
    }

    #[cfg(windows)]
    #[test]
    fn windows_comparison_is_case_insensitive_and_component_aware() {
        assert!(windows_path_is_within(
            Path::new("C:/Root"),
            Path::new("c:/root/Child.txt")
        ));
        assert!(windows_paths_equal(
            Path::new("C:/Root/File.txt"),
            Path::new("c:/root/file.TXT")
        ));
        assert!(!windows_path_is_within(
            Path::new("C:/Root"),
            Path::new("c:/root-sibling/file.txt")
        ));
    }

    #[test]
    fn file_authorization_accepts_canonical_alias_but_not_descendants() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("root");
        let allowed = root.join("allowed.txt");
        let child = root.join("allowed.txt.child");
        std::fs::create_dir(&root).unwrap();
        std::fs::write(&allowed, b"ok").unwrap();
        std::fs::write(&child, b"no").unwrap();

        let auth = PathAuthorizer::default();
        let alias = root.join("nested").join("..").join("allowed.txt");
        auth.authorize_file(&allowed).unwrap();

        assert!(auth.is_authorized(&alias));
        assert!(!auth.is_authorized(&child));
    }

    #[test]
    fn authorization_rejects_missing_paths() {
        let temp = tempfile::tempdir().unwrap();
        let missing = temp.path().join("missing.txt");
        let error = PathAuthorizer::default()
            .authorize_file(&missing)
            .unwrap_err();

        assert_eq!(error.code, "PATH_NOT_FOUND");
    }

    #[test]
    fn authorization_eviction_removes_oldest_grant() {
        let temp = tempfile::tempdir().unwrap();
        let mut paths = Vec::with_capacity(MAX_AUTHORIZED_PATHS + 1);
        for index in 0..=MAX_AUTHORIZED_PATHS {
            let path = temp.path().join(format!("file-{index}.txt"));
            std::fs::write(&path, b"ok").unwrap();
            paths.push(path);
        }

        let auth = PathAuthorizer::default();
        for path in &paths {
            auth.authorize_file(path).unwrap();
        }

        assert!(!auth.is_authorized(&paths[0]));
        assert!(auth.is_authorized(paths.last().unwrap()));
    }

    #[test]
    fn rename_validation_requires_authorized_files_and_output_directory() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("a.txt");
        let output = temp.path().join("out");
        std::fs::write(&source, b"a").unwrap();
        std::fs::create_dir(&output).unwrap();
        let auth = PathAuthorizer::default();
        assert_eq!(
            validate_rename_params(
                "rename.execute",
                &json!({"files":[source],"output_dir":output}),
                &auth
            )
            .unwrap_err()
            .code,
            "PATH_NOT_AUTHORIZED"
        );
    }

    #[test]
    fn rename_validation_accepts_authorized_regular_files_and_directory() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("a.txt");
        let output = temp.path().join("out");
        std::fs::write(&source, b"a").unwrap();
        std::fs::create_dir(&output).unwrap();
        let auth = PathAuthorizer::default();
        auth.authorize_file(&source).unwrap();
        auth.authorize_directory(&output).unwrap();

        assert!(validate_rename_params(
            "rename.execute",
            &json!({"files":[source],"output_dir":output}),
            &auth
        )
        .is_ok());
    }

    #[test]
    fn rename_validation_allows_empty_optional_output_directory() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("a.txt");
        std::fs::write(&source, b"a").unwrap();
        let auth = PathAuthorizer::default();
        auth.authorize_file(&source).unwrap();

        assert!(validate_rename_params(
            "rename.execute",
            &json!({"files":[source],"output_dir":""}),
            &auth
        )
        .is_ok());
    }

    #[test]
    fn rename_validation_rejects_directory_sources() {
        let temp = tempfile::tempdir().unwrap();
        let source_dir = temp.path().join("source");
        let output = temp.path().join("out");
        std::fs::create_dir(&source_dir).unwrap();
        std::fs::create_dir(&output).unwrap();
        let auth = PathAuthorizer::default();
        auth.authorize_directory(&source_dir).unwrap();
        auth.authorize_directory(&output).unwrap();

        let error = validate_rename_params(
            "rename.execute",
            &json!({"files":[source_dir],"output_dir":output}),
            &auth,
        )
        .unwrap_err();

        assert_eq!(error.code, "PATH_NOT_AUTHORIZED");
    }

    #[test]
    fn rename_validation_rejects_files_over_500_mib() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("large.bin");
        let output = temp.path().join("out");
        std::fs::File::create(&source)
            .unwrap()
            .set_len(MAX_GENERIC_INPUT_FILE_SIZE + 1)
            .unwrap();
        std::fs::create_dir(&output).unwrap();
        let auth = PathAuthorizer::default();
        auth.authorize_file(&source).unwrap();
        auth.authorize_directory(&output).unwrap();

        let error = validate_rename_params(
            "rename.execute",
            &json!({"files":[source],"output_dir":output}),
            &auth,
        )
        .unwrap_err();

        assert_eq!(error.code, "PATH_NOT_AUTHORIZED");
    }

    #[test]
    fn rename_validation_ignores_paths_for_non_rename_methods() {
        let error = validate_rename_params(
            "ping",
            &json!({"files":["C:/not-authorized.txt"],"output_dir":"C:/nope"}),
            &PathAuthorizer::default(),
        );

        assert!(error.is_ok());
    }

    #[test]
    fn stat_path_returns_authorized_metadata_and_structured_failures() {
        let temp = tempfile::tempdir().unwrap();
        let file = temp.path().join("file.txt");
        let missing = temp.path().join("missing.txt");
        std::fs::write(&file, b"hello").unwrap();
        let auth = PathAuthorizer::default();
        auth.authorize_file(&file).unwrap();

        let success = stat_path(&file.to_string_lossy(), &auth);
        assert_eq!(
            serde_json::to_value(success).unwrap(),
            json!({"ok":true,"value":{"path":file.to_string_lossy(),"isFile":true,"isDirectory":false,"size":5}})
        );

        let failure = stat_path(&missing.to_string_lossy(), &auth);
        assert_eq!(
            serde_json::to_value(failure).unwrap(),
            json!({"ok":false,"error":{"code":"unauthorized","message":"路径尚未授权","path":missing.to_string_lossy()}})
        );
    }
}
