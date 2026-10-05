Language data from [tesseract-ocr/tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast), revision `87416418657359cb625c412a48b6e1d6d41c29bd`, Apache-2.0 (`LICENSE` in this directory).

Run `.venv/Scripts/python.exe scripts/setup_ocr.py` to install or verify `eng.traineddata` and `chi_sim.traineddata`. The script pins SHA-256 checksums. Runtime OCR is entirely offline. Binary language data are generated dependencies, included by `engine/engine.spec` during packaging.
