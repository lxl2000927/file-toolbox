use super::error::DesktopError;

#[derive(Debug, Clone, PartialEq)]
pub enum IncomingMessage {
    Ready,
    Response {
        id: u64,
        result: Result<serde_json::Value, DesktopError>,
    },
    Notification {
        method: String,
        params: serde_json::Value,
    },
}

pub fn parse_line(line: &str) -> Result<IncomingMessage, DesktopError> {
    let value: serde_json::Value = serde_json::from_str(line)
        .map_err(|error| DesktopError::protocol(format!("无法解析引擎消息: {error}")))?;
    let object = value
        .as_object()
        .ok_or_else(|| DesktopError::protocol("引擎消息必须是 JSON 对象"))?;

    if let Some(id) = object.get("id").and_then(serde_json::Value::as_u64) {
        if let Some(error) = object.get("error") {
            let message = error
                .get("message")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("Engine error");
            return Ok(IncomingMessage::Response {
                id,
                result: Err(DesktopError::new("ENGINE_REQUEST_FAILED", message)),
            });
        }
        return Ok(IncomingMessage::Response {
            id,
            result: Ok(object
                .get("result")
                .cloned()
                .unwrap_or(serde_json::Value::Null)),
        });
    }

    let method = object
        .get("method")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| DesktopError::protocol("引擎通知缺少 method"))?;
    if method == "ready" {
        return Ok(IncomingMessage::Ready);
    }
    Ok(IncomingMessage::Notification {
        method: method.to_owned(),
        params: object
            .get("params")
            .cloned()
            .unwrap_or_else(|| serde_json::json!({})),
    })
}

pub fn encode_request(
    id: u64,
    method: &str,
    params: serde_json::Value,
    auth: &str,
) -> Result<String, DesktopError> {
    serde_json::to_string(&serde_json::json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": method,
        "params": params,
        "auth": auth,
    }))
    .map_err(|error| DesktopError::protocol(format!("无法编码引擎请求: {error}")))
}

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
            IncomingMessage::Response {
                id: 7,
                result: Ok(json!({"pong": true})),
            },
        );
        let message =
            parse_line(r#"{"jsonrpc":"2.0","id":8,"error":{"code":-32000,"message":"boom"}}"#)
                .unwrap();
        assert!(matches!(
            message,
            IncomingMessage::Response {
                id: 8,
                result: Err(_)
            }
        ));
    }

    #[test]
    fn parses_engine_notification() {
        assert_eq!(
            parse_line(r#"{"jsonrpc":"2.0","method":"task.progress","params":{"current":1}}"#)
                .unwrap(),
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
