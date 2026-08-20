"""Keep PyMuPDF diagnostics out of the engine JSON-RPC stdout channel."""

import os


os.environ["PYMUPDF_MESSAGE"] = "logging:name=file_toolbox.pymupdf,level=30"
os.environ["PYMUPDF_LOG"] = "logging:name=file_toolbox.pymupdf,level=20"
