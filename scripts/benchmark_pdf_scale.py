"""Native pipeline timings / source memory for the generated large corpus."""
import ctypes
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pymupdf as fitz
from src.core.pdf_tools_engine import run_pdf_tool


def memory_mb():
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in
            ['PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage']]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL('kernel32'); kernel.GetCurrentProcess.restype = wintypes.HANDLE
    get = ctypes.WinDLL('psapi').GetProcessMemoryInfo
    get.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not get(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb): raise ctypes.WinError()
    return {key: round(getattr(counters, field) / 1024**2, 1) for key, field in
            [('working_mb', 'WorkingSetSize'), ('peak_working_mb', 'PeakWorkingSetSize'), ('private_mb', 'PrivateUsage')]}


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    files = [str(ROOT / 'acceptance-samples' / 'scale-inputs' / f'document-{i + 1:04d}.pdf') for i in range(count)]
    report = {'files': count, 'bytes': sum(os.path.getsize(p) for p in files), 'baseline': memory_mb()}
    start = time.perf_counter()
    inspected = run_pdf_tool('inspect', files, {'compact_inspect': True})
    report['inspect_seconds'] = round(time.perf_counter() - start, 3)
    report['after_inspect'] = memory_mb()
    signatures = [s['signature'] for s in inspected['sources']]
    start = time.perf_counter()
    preview = run_pdf_tool('thumbnails', files, {'signatures': signatures, 'pages': [{'source': count - 1, 'index': 2}]})
    assert len(preview['thumbnails']) == 1
    report['last_page_preview_seconds'] = round(time.perf_counter() - start, 3)
    report['after_preview'] = memory_mb()
    # Merge the entire smaller workload; the desktop test covers 3000-file UI.
    if '--merge' in sys.argv:
        start = time.perf_counter()
        merged = run_pdf_tool('assemble', files, {'signatures': signatures,
            'output_dir': str(ROOT / 'acceptance-samples' / 'scale-results'), 'filename': f'merged-{count}', 'compression': 'none'})
        report['merge_seconds'] = round(time.perf_counter() - start, 3)
        report['after_merge'] = memory_mb()
        with fitz.open(merged['output_files'][0]) as doc:
            assert len(doc) == count * 3
            assert f'DOC {count:04d} PAGE 3' in doc[-1].get_text()
        report['merged_pages'] = count * 3
        report['output_bytes'] = merged['bytes_after']
    evidence = ROOT / 'acceptance-samples' / 'scale-evidence'
    evidence.mkdir(exist_ok=True)
    (evidence / f'native-{count}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__': main()
