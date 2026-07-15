import { spawn, ChildProcess } from "child_process";
import { createInterface } from "readline";

type JsonRpcResponse = {
  jsonrpc: "2.0";
  result?: any;
  error?: { code: number; message: string; data?: string };
  id: number | null;
};

type JsonRpcNotification = {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
};

type NotificationHandler = (method: string, params: any) => void;
type ExitHandler = (err: Error) => void;

export class PythonBridge {
  private process: ChildProcess | null = null;
  private reqId = 0;
  private pending = new Map<
    number,
    { resolve: (value: any) => void; reject: (err: Error) => void }
  >();
  private notificationHandlers = new Set<NotificationHandler>();
  private readyResolver: (() => void) | null = null;
  private readyRejecter: ((err: Error) => void) | null = null;
  private _starting = false;
  private _startTimer: ReturnType<typeof setTimeout> | null = null;
  private exitHandlers = new Set<ExitHandler>();
  private authToken = "";
  private _shuttingDown = false;

  private rejectPending(err: Error): void {
    this.pending.forEach(({ reject }) => reject(err));
    this.pending.clear();
  }

  private clearProcess(): void {
    this.process = null;
  }

  private notifyExit(err: Error): void {
    // 主动 shutdown 时跳过 exit handler，避免向渲染进程发送 engine.status: error
    if (this._shuttingDown) return;
    this.exitHandlers.forEach((handler) => {
      try {
        handler(err);
      } catch (handlerErr) {
        console.error("[python-bridge] exit handler error", handlerErr);
      }
    });
  }

  async start(
    enginePath: string,
    isDev = false,
    pythonExe = "python",
    authToken = "",
  ): Promise<void> {
    // 优先使用传入的 token，其次读取环境变量，最后为空（server.py 端会跳过空 token 检查）
    this.authToken = authToken || process.env.FILE_TOOLBOX_ENGINE_TOKEN || "";
    if (this._starting) throw new Error("PythonBridge 启动中，禁止重复调用");
    if (this.process) throw new Error("PythonBridge 已启动，禁止重复启动");
    this._starting = true;
    return new Promise((resolve, reject) => {
      const args = isDev ? [enginePath] : [];
      const command = isDev ? pythonExe : enginePath;

      console.log(`[python-bridge] 启动: ${command} ${args.join(" ")}`);

      this.process = spawn(command, args, {
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
        env: {
          ...process.env,
          FILE_TOOLBOX_ENGINE_TOKEN: authToken,
          FILE_TOOLBOX_ENGINE_DEBUG_ERRORS: isDev ? "1" : "0",
        },
      });

      const rl = createInterface({ input: this.process.stdout! });
      rl.on("line", (line: string) => this._handleLine(line));

      this.process.stderr!.on("data", (data: Buffer) => {
        console.error("[python]", data.toString().trimEnd());
      });

      this.readyRejecter = reject;

      this.process.on("error", (err) => {
        this._starting = false;
        if (this._startTimer) { clearTimeout(this._startTimer); this._startTimer = null; }
        if (this.readyRejecter) {
          const rej = this.readyRejecter;
          this.readyResolver = null;
          this.readyRejecter = null;
          rej(err);
        }
        this.clearProcess();
        this.rejectPending(err);
        this.notifyExit(err);
      });

      this.process.on("exit", (code) => {
        console.error(`[python] 进程退出，code=${code}`);
        this._starting = false;
        if (this._startTimer) { clearTimeout(this._startTimer); this._startTimer = null; }
        const err = new Error(`Python 进程意外退出 (code=${code})`);
        if (this.readyRejecter) {
          const rej = this.readyRejecter;
          this.readyResolver = null;
          this.readyRejecter = null;
          rej(new Error(`Python 进程启动后退出 (code=${code})`));
        }
        this.clearProcess();
        this.rejectPending(err);
        this.notifyExit(err);
      });

      this._startTimer = setTimeout(() => {
        if (this.readyResolver) {
          this._starting = false;
          this._startTimer = null;
          this.readyResolver = null;
          this.readyRejecter = null;
          try {
            this.process?.kill();
          } catch {
            // ignore
          }
          this.clearProcess();
          reject(new Error("Python 引擎启动超时 (30s)"));
        }
      }, 30000);

      this.readyResolver = () => {
        this._starting = false;
        if (this._startTimer) { clearTimeout(this._startTimer); this._startTimer = null; }
        this.readyRejecter = null;
        resolve();
      };
    });
  }

  async call(method: string, params: any = {}, timeout?: number): Promise<any> {
    if (!this.process) throw new Error("PythonBridge 未启动");
    if (!this.process.stdin || this.process.killed) throw new Error("PythonBridge 不可用");
    this.reqId = this.reqId >= Number.MAX_SAFE_INTEGER ? 1 : this.reqId + 1;
    const id = this.reqId;
    const request = JSON.stringify(
      { jsonrpc: "2.0", id, method, params, auth: this.authToken },
      (_key, value) => typeof value === "number" && !Number.isFinite(value) ? null : value,
    );
    // 默认 120s；已知同步长操作（rename.execute）延长到 300s
    // [Bug#3 Fix] 移除已下线的 pdf_split.execute（P0#4 改为 execute_async，异步立即返回）
    const LONG_RUN_METHODS = new Set(["rename.execute"]);
    const timeoutMs = timeout ?? (method === "task.cancel" ? 10000 : LONG_RUN_METHODS.has(method) ? 300000 : 120000);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          // 超时后尝试通知 Python 取消任务（若有 task_id），失败忽略
          const taskId = (params as any)?.task_id;
          if (taskId) {
            this.call("task.cancel", { task_id: taskId }).catch(() => {});
          }
          reject(new Error(`请求超时: ${method} (${timeoutMs / 1000}s)`));
        }
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (err) => { clearTimeout(timer); reject(err); },
      });
      this.process!.stdin!.write(request + "\n", (err) => {
        if (err && this.pending.has(id)) {
          this.pending.delete(id);
          clearTimeout(timer);
          reject(err);
        }
      });
    });
  }

  addNotificationHandler(handler: NotificationHandler): () => void {
    this.notificationHandlers.add(handler);
    return () => this.notificationHandlers.delete(handler);
  }

  addExitHandler(handler: ExitHandler): () => void {
    this.exitHandlers.add(handler);
    return () => this.exitHandlers.delete(handler);
  }

  async shutdown(): Promise<void> {
    if (!this.process) {
      this.notificationHandlers.clear();
      this.exitHandlers.clear();
      return;
    }
    if (this._shuttingDown) return;  // 防止 window-all-closed + before-quit 双重 shutdown
    this._shuttingDown = true;
    this.rejectPending(new Error("PythonBridge 已关闭"));
    // 尝试优雅关闭：发送 shutdown 通知，等 1.5s 让 Python 写完 history.json
    try {
      this.call("shutdown", {}, 2000).catch(() => {});
      await new Promise((r) => setTimeout(r, 1500));
    } catch {
      // ignore：server.py 可能没有 shutdown 方法，重点是不阻塞 kill 流程
    }
    try {
      this.process.kill();
    } catch {
      // ignore
    }
    this.process = null;
    this.notificationHandlers.clear();
    this.exitHandlers.clear();
  }

  private _handleLine(line: string): void {
    line = line.trim();
    if (!line) return;

    let msg: JsonRpcResponse | JsonRpcNotification;
    try {
      const parsed: unknown = JSON.parse(line);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        console.warn("[python-bridge] 忽略非对象 JSON 消息", line.slice(0, 120));
        return;
      }
      msg = parsed as JsonRpcResponse | JsonRpcNotification;
    } catch {
      console.warn("[python-bridge] 无法解析响应行（非 JSON）:", line.slice(0, 120));
      return;
    }

    if (!("id" in msg) || msg.id == null) {
      const method = "method" in msg && typeof msg.method === "string" ? msg.method : "";
      const params = "params" in msg ? msg.params : undefined;
      if (method === "ready") {
        if (this.readyResolver) {
          const r = this.readyResolver;
          this.readyResolver = null;
          r();
        }
        return;
      }
      if (method) {
        this.notificationHandlers.forEach((h) => {
          try {
            h(method, params);
          } catch (err) {
            console.error("[python-bridge] notification handler error", err);
          }
        });
      }
      return;
    }

    const pending = this.pending.get(msg.id);
    if (!pending) {
      console.warn(`[python-bridge] 未知 id 的响应: ${msg.id}`);
      return;
    }
    this.pending.delete(msg.id);
    if (msg.error) {
      const err = Object.assign(new Error(msg.error.message || "Engine error"), {
        code: msg.error.code,
        data: msg.error.data,
      });
      pending.reject(err);
    } else {
      pending.resolve(msg.result);
    }
  }
}
