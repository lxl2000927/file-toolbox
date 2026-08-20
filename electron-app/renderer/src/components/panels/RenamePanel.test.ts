// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import RenamePanel from "./RenamePanel.vue";

vi.mock("../../composables/useAppDialog", () => ({
  useAppDialog: () => ({ confirm: vi.fn().mockResolvedValue(true) }),
}));

describe("RenamePanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the file size returned by the desktop bridge", async () => {
    const path = "C:\\fixtures\\alpha.txt";
    Object.defineProperty(window, "electronAPI", {
      configurable: true,
      value: {
        openFileDialog: vi.fn().mockResolvedValue([path]),
        statPaths: vi.fn().mockResolvedValue([
          {
            ok: true,
            value: { path, isFile: true, isDirectory: false, size: 1536 },
          },
        ]),
        onFileDrop: vi.fn(() => () => {}),
      },
    });
    Object.defineProperty(window, "engine", {
      configurable: true,
      value: {
        rename: {
          preview: vi.fn().mockResolvedValue([]),
        },
      },
    });

    const wrapper = mount(RenamePanel);
    await wrapper.get(".toolbar .btn-primary").trigger("click");
    await flushPromises();

    const fileRow = wrapper.findAll("tbody tr").find((row) => row.text().includes("alpha.txt"));
    expect(fileRow?.text()).toContain("1.5 KB");

    wrapper.unmount();
  });

  it("refreshes the restored file size after undoing an overwrite rename", async () => {
    const originalPath = "C:\\fixtures\\alpha.txt";
    const renamedPath = "C:\\fixtures\\alpha_gui.txt";
    const statPaths = vi.fn(async (paths: string[]) => paths.map((path) => ({
      ok: true as const,
      value: { path, isFile: true, isDirectory: false, size: path === originalPath ? 14 : 21 },
    })));
    Object.defineProperty(window, "electronAPI", {
      configurable: true,
      value: {
        openFileDialog: vi.fn().mockResolvedValue([originalPath]),
        statPaths,
        onFileDrop: vi.fn(() => () => {}),
      },
    });
    Object.defineProperty(window, "engine", {
      configurable: true,
      value: {
        rename: {
          preview: vi.fn().mockImplementation(async (paths: string[]) => paths.map((path) => ({
            original_path: path,
            new_name: path === originalPath ? "alpha_gui.txt" : "alpha_gui_gui.txt",
          }))),
          execute: vi.fn().mockResolvedValue({
            total: 1,
            successful: 1,
            failed: 0,
            errors: [],
            operations: [{
              original_path: originalPath,
              new_path: renamedPath,
              operation_type: "rename",
              success: true,
            }],
            undo_token: "undo-1",
          }),
          undo: vi.fn().mockResolvedValue({
            restored: [{ from: renamedPath, to: originalPath, operation: "rename" }],
            failed: [],
          }),
        },
      },
    });

    const wrapper = mount(RenamePanel);
    await wrapper.get(".toolbar .btn-primary").trigger("click");
    await flushPromises();
    await wrapper.get('input[placeholder="插入字符"]').setValue("_gui");
    await flushPromises();

    const startButton = wrapper.findAll("button").find((button) => button.text() === "开始重命名");
    await startButton?.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("alpha_gui.txt");
    expect(wrapper.text()).toContain("21 B");

    const undoButton = wrapper.findAll("button").find((button) => button.text() === "撤销");
    await undoButton?.trigger("click");
    await flushPromises();

    const restoredRow = wrapper.findAll("tbody tr").find((row) => row.text().includes("alpha.txt"));
    expect(restoredRow?.text()).toContain("14 B");
    expect(statPaths).toHaveBeenLastCalledWith([originalPath]);

    wrapper.unmount();
  });
});
