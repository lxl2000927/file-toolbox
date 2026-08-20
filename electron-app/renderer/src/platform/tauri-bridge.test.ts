import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createTauriBridge,
  installDesktopBridge,
} from "./tauri-bridge";
import {
  electronCapabilities,
  tauriPhaseTwoCapabilities,
} from "./runtime";

const tauriCore = vi.hoisted(() => ({
  invoke: vi.fn(),
  isTauri: vi.fn(),
}));

const tauriEvent = vi.hoisted(() => ({
  listen: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => tauriCore);
vi.mock("@tauri-apps/api/event", () => tauriEvent);

const notMigrated = {
  code: "NOT_MIGRATED",
  message: "该功能尚未迁移到 Tauri",
};

const unsupportedUpdateStatus = {
  state: "unsupported",
  supported: false,
  packageType: "development",
  portable: false,
  current: "2.5.0",
};

describe("Tauri compatibility bridge", () => {
  it("translates engine status, ping, and rename calls exactly", async () => {
    const invoke = vi.fn().mockResolvedValue([]);
    const bridge = createTauriBridge({ invoke, listen: vi.fn() });
    const files = ["C:\\a.txt"];
    const rules = [{ type: "uniform_name" as const, base_name: "b" }];

    await bridge.engine.status();
    await bridge.engine.ping();
    await bridge.engine.rename.preview(files, rules);
    await bridge.engine.rename.execute(files, rules, "overwrite", "C:\\out");
    await bridge.engine.rename.undo("undo-1");

    expect(invoke).toHaveBeenNthCalledWith(1, "engine_status");
    expect(invoke).toHaveBeenNthCalledWith(2, "engine_call", {
      method: "ping",
      params: {},
    });
    expect(invoke).toHaveBeenNthCalledWith(3, "engine_call", {
      method: "rename.preview",
      params: { files, rules },
    });
    expect(invoke).toHaveBeenNthCalledWith(4, "engine_call", {
      method: "rename.execute",
      params: {
        files,
        rules,
        save_method: "overwrite",
        output_dir: "C:\\out",
      },
    });
    expect(invoke).toHaveBeenNthCalledWith(5, "engine_call", {
      method: "rename.undo",
      params: { undo_token: "undo-1" },
    });
  });

  it("preserves Electron rename defaults in translated execute calls", async () => {
    const invoke = vi.fn().mockResolvedValue({});
    const bridge = createTauriBridge({ invoke, listen: vi.fn() });

    await bridge.engine.rename.execute([], []);

    expect(invoke).toHaveBeenCalledWith("engine_call", {
      method: "rename.execute",
      params: {
        files: [],
        rules: [],
        save_method: "copy",
        output_dir: "",
      },
    });
  });

  it("translates PDF split and cancellation calls exactly", async () => {
    const invoke = vi.fn().mockResolvedValue({});
    const bridge = createTauriBridge({ invoke, listen: vi.fn() });
    const config = { mode: "by_page_count" as const, page_count: 2, output_dir: "C:\\out" };

    await bridge.engine.pdfSplit.validate("C:\\a.pdf");
    await bridge.engine.pdfSplit.preview("C:\\a.pdf", config);
    await bridge.engine.pdfSplit.previewMany(["C:\\a.pdf"], config);
    await bridge.engine.pdfSplit.executeAsync(["C:\\a.pdf"], config, "pdf-task-1");
    await bridge.engine.cancelTask("pdf-task-1");

    expect(invoke.mock.calls).toEqual([
      ["engine_call", { method: "pdf_split.validate", params: { pdf_path: "C:\\a.pdf" } }],
      ["engine_call", { method: "pdf_split.preview", params: { pdf_path: "C:\\a.pdf", config } }],
      ["engine_call", { method: "pdf_split.preview_many", params: { pdf_paths: ["C:\\a.pdf"], config } }],
      ["engine_call", { method: "pdf_split.execute_async", params: { pdf_paths: ["C:\\a.pdf"], config, task_id: "pdf-task-1" } }],
      ["engine_call", { method: "task.cancel", params: { task_id: "pdf-task-1" } }],
    ]);
  });

  it("maps file commands and restart to their Rust parameter shapes", async () => {
    const invoke = vi.fn().mockResolvedValue([]);
    const bridge = createTauriBridge({ invoke, listen: vi.fn() });
    const fileOptions = {
      filters: [{ name: "Text", extensions: ["txt"] }],
      multi: true,
      title: "Choose files",
    };

    await bridge.electron.openFileDialog(fileOptions);
    await bridge.electron.openDirectoryDialog({ title: "Choose directory" });
    await bridge.electron.statPaths(["C:\\a.txt"]);
    await bridge.electron.restartEngine();

    expect(invoke).toHaveBeenNthCalledWith(1, "open_files", {
      options: fileOptions,
    });
    expect(invoke).toHaveBeenNthCalledWith(2, "open_directory", {
      title: "Choose directory",
    });
    expect(invoke).toHaveBeenNthCalledWith(3, "stat_paths", {
      paths: ["C:\\a.txt"],
    });
    expect(invoke).toHaveBeenNthCalledWith(4, "engine_restart");
  });

  it("deactivates notifications immediately and unlistens after registration", async () => {
    const unlisten = vi.fn();
    let handler: ((event: { payload: { method: string; params: {} } }) => void) | undefined;
    let resolveListen: ((unlisten: () => void) => void) | undefined;
    const listen = vi.fn((_name, nextHandler) => {
      handler = nextHandler;
      return new Promise<() => void>((resolve) => {
        resolveListen = resolve;
      });
    });
    const bridge = createTauriBridge({ invoke: vi.fn(), listen });
    const callback = vi.fn();

    const dispose = bridge.engine.onNotification(callback);
    expect(listen).toHaveBeenCalledWith("engine-notification", expect.any(Function));
    dispose();
    handler?.({ payload: { method: "engine.status", params: {} } });
    expect(callback).not.toHaveBeenCalled();

    resolveListen?.(unlisten);
    await Promise.resolve();
    expect(unlisten).toHaveBeenCalledOnce();
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
    expect(listen).toHaveBeenCalledWith("desktop-file-drop", expect.any(Function));
    expect(callback).toHaveBeenCalledWith(["C:\\dropped.txt"]);

    dispose?.();
    await Promise.resolve();
    expect(unlisten).toHaveBeenCalledOnce();
  });

  it("rejects every unsupported engine operation with NOT_MIGRATED", async () => {
    const bridge = createTauriBridge({ invoke: vi.fn(), listen: vi.fn() });
    const calls = [
      bridge.engine.scanSplit.previewReference("reference.png"),
      bridge.engine.scanSplit.probePage({
        pdfPath: "a.pdf",
        referenceImagePath: "reference.png",
        options: {},
        pageIndex: 0,
      }),
      bridge.engine.scanSplit.scanOnly({
        pdfPath: "a.pdf",
        referenceImagePath: "reference.png",
        options: {},
        pageLimit: 1,
      }),
      bridge.engine.scanSplit.executeAsync({
        pdfPath: "a.pdf",
        referenceImagePath: "reference.png",
        options: {},
      }),
    ];

    for (const call of calls) {
      await expect(call).rejects.toEqual(notMigrated);
    }
  });

  it("returns non-throwing history fallbacks", async () => {
    const bridge = createTauriBridge({ invoke: vi.fn(), listen: vi.fn() });

    await expect(bridge.engine.history.get()).resolves.toEqual({
      records: [],
      session_id: "",
    });
    await expect(bridge.engine.history.clear()).resolves.toEqual({
      cleared: false,
      session_id: "",
      error: notMigrated.message,
    });
  });

  it("returns the exact unsupported update contract", async () => {
    const bridge = createTauriBridge({ invoke: vi.fn(), listen: vi.fn() });

    await expect(bridge.electron.update.getStatus()).resolves.toEqual(unsupportedUpdateStatus);
    await expect(bridge.electron.update.check()).resolves.toEqual(unsupportedUpdateStatus);
    await expect(bridge.electron.update.download()).resolves.toEqual(unsupportedUpdateStatus);
    await expect(bridge.electron.update.install()).resolves.toEqual({
      accepted: false,
      status: unsupportedUpdateStatus,
    });
    expect(bridge.electron.update.onStatus(vi.fn())).toBeTypeOf("function");
  });

  it("returns structured file-access fallbacks and native-drop placeholders", async () => {
    const bridge = createTauriBridge({ invoke: vi.fn(), listen: vi.fn() });

    await expect(bridge.electron.readDirFiles("C:\\dir")).resolves.toEqual({
      ok: false,
      error: {
        code: "unsupported_type",
        message: notMigrated.message,
        path: "C:\\dir",
      },
    });
    await expect(bridge.electron.getFilePreviewUrl("C:\\a.pdf")).resolves.toEqual({
      ok: false,
      error: {
        code: "unsupported_type",
        message: notMigrated.message,
        path: "C:\\a.pdf",
      },
    });
    expect(bridge.electron.getPathForFile({} as File)).toBe("");
    await expect(bridge.electron.getPathsForFiles([])).resolves.toEqual([]);
  });

  it("rejects unsupported Electron side effects with NOT_MIGRATED", async () => {
    const bridge = createTauriBridge({ invoke: vi.fn(), listen: vi.fn() });

    await expect(bridge.electron.openExternal("https://example.com")).rejects.toEqual(notMigrated);
    await expect(bridge.electron.openDataDir()).rejects.toEqual(notMigrated);
    await expect(bridge.electron.saveFile({ content: "text" })).rejects.toEqual(notMigrated);
  });
});

describe("desktop bridge installation", () => {
  beforeEach(() => {
    vi.stubGlobal("window", {});
    tauriCore.invoke.mockReset();
    tauriCore.isTauri.mockReset();
    tauriEvent.listen.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves existing Electron globals exactly outside Tauri", async () => {
    const existingEngine = { status: vi.fn() };
    const existingElectron = { openFileDialog: vi.fn() };
    window.engine = existingEngine as unknown as typeof window.engine;
    window.electronAPI = existingElectron as unknown as typeof window.electronAPI;
    tauriCore.isTauri.mockReturnValue(false);

    await installDesktopBridge();

    expect(window.engine).toBe(existingEngine);
    expect(window.electronAPI).toBe(existingElectron);
    expect(window.desktopRuntime).toEqual({
      kind: "electron",
      capabilities: electronCapabilities,
    });
    expect(tauriEvent.listen).not.toHaveBeenCalled();
  });

  it("installs injected Tauri APIs and phase-two capabilities in Tauri", async () => {
    tauriCore.isTauri.mockReturnValue(true);
    tauriCore.invoke.mockResolvedValue({ pong: true });
    tauriEvent.listen.mockResolvedValue(vi.fn());

    await installDesktopBridge();
    await window.engine?.ping();

    expect(tauriCore.invoke).toHaveBeenCalledWith("engine_call", {
      method: "ping",
      params: {},
    });
    expect(window.electronAPI).toBeDefined();
    expect(window.desktopRuntime).toEqual({
      kind: "tauri",
      capabilities: tauriPhaseTwoCapabilities,
    });
  });
});
