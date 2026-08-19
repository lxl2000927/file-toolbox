import { describe, expect, it } from "vitest";
import { panelIsEnabled, sanitizePanel, tauriPhaseOneCapabilities } from "./runtime";

describe("phase-one capabilities", () => {
  it("enables only migrated functional panels", () => {
    expect(panelIsEnabled("rename", tauriPhaseOneCapabilities)).toBe(true);
    expect(panelIsEnabled("pdf_split", tauriPhaseOneCapabilities)).toBe(false);
    expect(panelIsEnabled("scan_split", tauriPhaseOneCapabilities)).toBe(false);
    expect(panelIsEnabled("about", tauriPhaseOneCapabilities)).toBe(true);
  });

  it("falls back from an unavailable saved panel", () => {
    expect(sanitizePanel("pdf_split", tauriPhaseOneCapabilities)).toBe("rename");
  });
});
