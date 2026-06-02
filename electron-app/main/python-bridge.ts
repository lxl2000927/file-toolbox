import { spawn, ChildProcess } from "child_process";
import { createInterface } from "readline";

type JsonRpcResponse = {
  jsonrpc: "2.0";
  result?: any;
  error?: { code: number; message: string; data?: string };
  id: number | null;
};

type NotificationHandler = (method: string, params: any) => void;

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

  private rejectPending(err: Error): void {
    this.pending.forEach(({ reject }) => reject(err));
    this.pending.clear();
  }

  private clearProcess(): void {
    this.process = null;
  }

  async start(
    enginePath: string,
    isDev = false,
    pythonExe = "python",
  ): Promise<void> {
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

  async call(method: string, params: any = {}): Promise<any> {
    if (!this.process) throw new Error("PythonBridge 未启动");
    if (!this.process.stdin || this.process.killed) throw new Error("PythonBridge 不可用");
    const id = ++this.reqId;
    if (!Number.isFinite(id)) this.reqId = 1;
    const safeParams = JSON.parse(JSON.stringify(params, (k, v) =>
      typeof v === "number" && !Number.isFinite(v) ? null : v
    ));
    const request = JSON.stringify({ jsonrpc: "2.0", id, method, params: safeParams });
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`请求超时: ${method} (60s)`));
        }
      }, 60000);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timeout); resolve(value); },
        reject: (err) => { clearTimeout(timeout); reject(err); },
      });
      this.process!.stdin!.write(request + "\n", (err) => {
        if (err && this.pending.has(id)) {
          this.pending.delete(id);
          clearTimeout(timeout);
          reject(err);
        }
      });
    });
  }

  addNotificationHandler(handler: NotificationHandler): () => void {
    this.notificationHandlers.add(handler);
    return () => this.notificationHandlers.delete(handler);
  }

  shutdown(): void {
    this.rejectPending(new Error("PythonBridge 已关闭"));
    if (this.process) {
      try {
        this.process.kill();
      } catch {
        // ignore
      }
      this.process = null;
    }
  }

  private _handleLine(line: string): void {
    line = line.trim();
    if (!line) return;

    let msg: JsonRpcResponse;
    try {
      msg = JSON.parse(line);
    } catch {
      console.warn("[python-bridge] 无法解析响应行（非 JSON）:", line.slice(0, 120));
      return;
    }

    if (msg.id == null) {
      const method = (msg as any).method;
      const params = (msg as any).params;
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
      pending.reject(new Error(msg.error.message));
    } else {
      pending.resolve(msg.result);
    }
  }
}
