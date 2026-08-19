export type DesktopCapabilities = {
  rename: boolean;
  pdfSplit: boolean;
  scanSplit: boolean;
  update: boolean;
};

export const electronCapabilities: DesktopCapabilities = {
  rename: true,
  pdfSplit: true,
  scanSplit: true,
  update: true,
};

export const tauriPhaseOneCapabilities: DesktopCapabilities = {
  rename: true,
  pdfSplit: false,
  scanSplit: false,
  update: false,
};
