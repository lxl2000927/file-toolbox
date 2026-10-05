# 3000-file PDF workspace implementation plan

**Goal:** Import, browse and process up to 3000 authorized PDF/image sources without keeping every source document open or rereading unrelated files for previews.

**Architecture:** Preserve the existing RPC and input identity checks. Inspect new files in small batches, publish completed batches to the UI, and lease one source document at a time in Python. Previews submit only visible sources with remapped indexes. Thumbnail and undo caches have explicit bounds.

**Tech stack:** Existing Vue / Electron / Python / PyMuPDF; no new dependencies.

**Authorization:** User approved the preceding five-point optimization proposal and explicitly requested implementation for at most 3000 files. Continue in the existing dirty workspace; no release or commit requested.

## Constraints and review focus

- 3000 unique workspace files, 100000 workspace pages, 10000 pages per source, existing per-file byte/pixel limits retained. Show limits before exceeding them.
- Read each newly imported file once per inspection; open at most one source document at a time. No aggregate-byte import restriction.
- Keep content-signature and authorized-handle validation on every opened input; never trust renderer-supplied metadata for filesystem authorization.
- Import cancellation retains completed batches; bad/encrypted files are reported while valid files remain usable.
- File identity changes, fast page switching, import during prior edits, selecting 3001 files, and partial cancellation require regression tests.

## Tasks

- [x] Backend: add failing tests for 3000-file inspection/merge, >3000 rejection, peak live source count, unrelated preview reads, partial inspection and stale signatures. Implement lazy source leases and compact inspection, adjust both RPC limits, retain atomic output and existing page/link behavior. Run focused Python and security tests.
- [x] Frontend: add failing component tests for visible-only preview requests, batched append without rereading old inputs, cancellation and partial failures. Implement incremental importer, bounded thumbnail LRU, stale-request cancellation, shallow large collections and bounded undo history. Run Node and type checks.
- [x] Acceptance: generate representative 1000/3000-file corpora, measure import/first preview/late-page/revisit and process memory through the real desktop bridge; verify real merged page count/order, cancellation, no renderer errors and bounded rendered cards. Run full existing suites/build/desktop acceptance.
- [x] Report: record workload sizes, measured values and limits; update user acceptance documentation. Do not generalize synthetic timings to arbitrary scan documents.

## Execution ledger

- Started from the current implementation: 64-file cap in Electron, RPC and engine; all sources reloaded for every preview, all source documents kept open until a request completes.

- Completed backend RED/GREEN regression: one live source, no unrelated reads, 3000 inputs, partial/cancelled import; small-file read request regression reproduced 536870913 bytes requested for a 790-byte file and fixed it.
- Completed frontend regressions: remapped visible-only requests, 16-file batches, duplicate suppression, import undo/redo, 100000-page bound, LRU byte/item bounds, cancellation and stale results.
- Ruling: preserve previously granted paths and canonical/dev/ino checks, but index direct file grants and directory grants separately to remove quadratic authorization scans.
- Ruling: preserve existing source retention on page undo/delete. Source lists remain until workspace clear, matching prior behavior and allowing redo.
- Validation: Python 114, Node 59 (including reviewer regression), typecheck/build; original 22 desktop checks. The first rapid-navigation run under simultaneous fixture generation exceeded its 500 ms assertion; a rerun with fixtures generated passed without changing app code or weakening the assertion.
- Real scale acceptance: 3000 PDFs / 9000 pages / 1.07 GB, first preview 0.754 s, import 6.313 s, late preview 0.373 s; no renderer long tasks or errors. Native merge validated all 9000 pages in 14.275 s; source processing peak working set ~66.5 MiB, full merge peak ~1145.7 MiB. Detailed evidence in docs/acceptance-large-pdf-2026-10-04.md.
- Independent read-only review found stale preview-error copy after a cache hit; reproduced with a failing test and cleared it before the early cache return. No verified critical/important findings at review time.
