"""One process-wide gate for MuPDF documents shared by all task workers."""
import threading

PDF_NATIVE_LOCK = threading.RLock()
