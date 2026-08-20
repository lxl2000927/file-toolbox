import { describe, expect, it } from "vitest";
import { panelIsEnabled, sanitizePanel, tauriPhaseThreeCapabilities } from "./runtime";

describe("phase-three capabilities", () => {
  it("enables rename, ordinary PDF split, and scan split without updates", () => {
    expect(panelIsEnabled("rename", tauriPhaseThreeCapabilities)).toBe(true);
    expect(panelIsEnabled("pdf_split", tauriPhaseThreeCapabilities)).toBe(true);
    expect(panelIsEnabled("scan_split", tauriPhaseThreeCapabilities)).toBe(true);
    expect(panelIsEnabled("about", tauriPhaseThreeCapabilities)).toBe(true);
  });

  it("keeps available saved PDF and scan panels", () => {
    expect(sanitizePanel("pdf_split", tauriPhaseThreeCapabilities)).toBe("pdf_split");
    expect(sanitizePanel("scan_split", tauriPhaseThreeCapabilities)).toBe("scan_split");
  });
});
