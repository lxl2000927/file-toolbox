# PDF workbench four-part refinement

**Goal:** Complete the four improvements explicitly approved by the user: usable output results, bulk page selection/movement, compression/OCR feedback, and large/error-path acceptance.

**Architecture:** Add focused result and bulk-selection components and pure helpers; retain the 3000-file incremental importer and bounded preview cache. Native result actions validate the app sender, authorized canonical file and document extension. OCR writes optional complete TXT beside the finished PDF through atomic output, while IPC carries only bounded page previews.

**Authorization:** User requested all four improvements after reviewing the earlier proposal. Continue in this existing workspace with its earlier changes. No release, installer or commit requested.

## Decisions

- Page expressions use current 1-based workspace positions, comma-separated pages/ranges; invalid expressions leave selection unchanged. Odd/even replace selection; invert toggles all workspace pages. Moving a stable selected group targets its final first position and supports undo/redo.
- Output results distinguish success, warnings, partial failure, cancellation and failure. Already produced files remain usable. File rows paginate at 50; native open/reveal/copy are explicit buttons.
- Raster presets: clear 220 DPI / 88 quality, balanced 150 / 78, compact 100 / 60; manual changes display custom. Structure optimization remains a separate mode. Compare bytes only for a complete full-page PDF result; subset/image/partial outputs must not claim compression savings.
- OCR: first 20 completed pages, at most 4000 characters each in the preview; show exact total/page truncation. Copy current preview; full UTF-8 TXT sidecar defaults on in workbench OCR and can be saved elsewhere. Existing scan review stays compatible.
- Retain per-file authorization/signature checks, new-file atomic publication and memory bounds. Never expose arbitrary shell execution or filesystem reads through result controls.

## Tasks and checks

- [x] Add RED tests for page parsing, stable positional moves and honest result summaries; implement helpers and components with responsive layout.
- [x] Add RED tests for native open/reveal/copy and denied paths/extensions/senders; implement narrow preload APIs.
- [x] Add RED tests for bounded OCR previews/full TXT, atomic sidecar failure, unwritable output, repeated cancellation/retry and compression comparisons; implement backend result metadata and streaming TXT.
- [x] Exercise new controls in the real desktop suite, rerun the mixed 1000/3000 corpus, record performance/memory and screenshots, run full regression/typecheck/build.
- [x] Independent focused review, resolve verified findings, update acceptance documentation.

## Ledger

- Starting verification baseline: Python 114, Node 59, desktop 22, plus 1000/3000-file scale acceptance.
- RED→GREEN: missing range/move helpers, result summary module and native result APIs; missing OCR preview/sidecar metadata. Nine backend refinement cases now pass; complete Python suite 123/123.
- RED→GREEN: 1000 overlapping full-page ranges on 100000 pages took about 1.5 s; difference counts reduce this to linear work. Regression budget 500 ms; focused test including fixture setup now 11–21 ms.
- Native result API tests cover denied paths/types/senders, replaced selected files, full TXT copy/cancel. Source files and OCR sidecars use existing atomic output publication; copy-to-save-dialog destination also uses a temporary sibling file before rename.
- Desktop 26/26: range/odd/even/invert/move/undo, actual PDF/OCR/compression/image output, full TXT copy, and previous scan/log/update/motion flows. Dialogs automated; external shell launch and clipboard OS boundaries recorded rather than changing the user's desktop/clipboard.
- Large corpus: 1000/3000 files (3000/9000 pages), repeated cancel three times and same-workspace resume; 4502-page selection, invert, move and undo passed. No renderer errors or import/preview long tasks >50 ms.
- Testing note: first extended scale run failed because the test injected a duplicate top-level JavaScript const; isolated injected locals in IIFEs and reran successfully. No application change was needed for that failure.
- Final independent review: fixed valid JPEG2000/JBIG2/portable-map outputs rejected by document actions; native-format test RED→GREEN and real JPEG2000 extraction covered. Executable files remain rejected.
- Final independent review: fixed queued cancellation without an engine result being reported as failure. Normalize cancellation in usePdfTools; preserve explicitly labeled previous results when the cancelled attempt produced nothing. Real component/composable queued notification regression RED→GREEN.
- Final wording correction: truncated preview reports its limit without claiming a TXT exists; the TXT section independently shows successful sidecars or how to enable full export.
- Final verification after review fixes: Python 124/124; Node 71/71; typecheck/build clean; desktop 26/26; 1000/3000 scale plus cancellation/retry/bulk move undo passed; git diff --check clean. Final 3000-file import 5506 ms, first preview 656 ms, last preview 377 ms; no renderer errors or measured import/preview long tasks.
- Acceptance report and screenshot inspection completed: docs/acceptance-workbench-refinement-2026-10-04.md. Changes remain in the existing workspace for user acceptance; no release or commit requested.
