// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import PdfSplitPanel from "./PdfSplitPanel.vue";

vi.mock("../../composables/useAppDialog", () => ({
  useAppDialog: () => ({ confirm: vi.fn().mockResolvedValue(true) }),
}));

describe("PdfSplitPanel native drops", () => {
  afterEach(() => vi.restoreAllMocks());

  it("adds unique PDF paths and disposes the native listener", async () => {
    let onDrop: ((paths: string[]) => void) | undefined;
    const dispose = vi.fn();
    Object.defineProperty(window, "electronAPI", {
      configurable: true,
      value: {
        onFileDrop: vi.fn((callback) => { onDrop = callback; return dispose; }),
        openFileDialog: vi.fn(),
        openDirectoryDialog: vi.fn(),
        getPathsForFiles: vi.fn().mockResolvedValue([]),
      },
    });
    const validate = vi.fn().mockResolvedValue({ valid: true, message: "OK", page_count: 1 });
    Object.defineProperty(window, "engine", {
      configurable: true,
      value: {
        pdfSplit: { validate, previewMany: vi.fn(), executeAsync: vi.fn() },
        cancelTask: vi.fn(),
        onNotification: vi.fn(() => () => {}),
      },
    });

    const wrapper = mount(PdfSplitPanel);
    onDrop?.(["C:\\a.pdf", "C:\\ignore.txt", "C:\\a.pdf"]);
    await flushPromises();

    expect(validate).toHaveBeenCalledTimes(1);
    expect(validate).toHaveBeenCalledWith("C:\\a.pdf");
    expect(wrapper.text()).toContain("a.pdf");
    wrapper.unmount();
    expect(dispose).toHaveBeenCalledOnce();
  });
});
