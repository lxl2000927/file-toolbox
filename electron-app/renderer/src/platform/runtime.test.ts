import { describe, expect, it } from "vitest";
import { panelIsEnabled, sanitizePanel, tauriPhaseTwoCapabilities } from "./runtime";

describe("phase-two capabilities", () => {
  it("enables rename and ordinary PDF split only", () => {
    expect(panelIsEnabled("rename", tauriPhaseTwoCapabilities)).toBe(true);
    expect(panelIsEnabled("pdf_split", tauriPhaseTwoCapabilities)).toBe(true);
    expect(panelIsEnabled("scan_split", tauriPhaseTwoCapabilities)).toBe(false);
    expect(panelIsEnabled("about", tauriPhaseTwoCapabilities)).toBe(true);
  });

  it("keeps an available saved PDF panel", () => {
    expect(sanitizePanel("pdf_split", tauriPhaseTwoCapabilities)).toBe("pdf_split");
    expect(sanitizePanel("scan_split", tauriPhaseTwoCapabilities)).toBe("rename");
  });
});
