export type DesktopCapabilities = {
  rename: boolean;
  pdfSplit: boolean;
  scanSplit: boolean;
  update: boolean;
};

export type PanelKey = "rename" | "pdf_split" | "scan_split" | "about";

export const electronCapabilities: DesktopCapabilities = {
  rename: true,
  pdfSplit: true,
  scanSplit: true,
  update: true,
};

export const tauriPhaseThreeCapabilities: DesktopCapabilities = {
  rename: true,
  pdfSplit: true,
  scanSplit: true,
  update: false,
};

export function panelIsEnabled(panel: PanelKey, caps: DesktopCapabilities): boolean {
  if (panel === "pdf_split") return caps.pdfSplit;
  if (panel === "scan_split") return caps.scanSplit;
  return true;
}

export function sanitizePanel(panel: string | null, caps: DesktopCapabilities): PanelKey {
  const candidate = (["rename", "pdf_split", "scan_split", "about"] as const).find((key) => key === panel);
  return candidate && panelIsEnabled(candidate, caps) ? candidate : "rename";
}
