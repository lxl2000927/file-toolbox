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
