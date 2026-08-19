#[derive(Debug, Clone, serde::Serialize, PartialEq, Eq)]
pub struct DesktopError {
    pub code: &'static str,
    pub message: String,
}

impl DesktopError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
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
