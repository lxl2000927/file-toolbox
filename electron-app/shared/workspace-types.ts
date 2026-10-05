export type WorkspaceScope = 'workbench' | 'scan';
export type WorkspaceSource = {
  path: string; name: string; kind: 'pdf' | 'image'; page_count: number; size: number; signature: string;
  role?: 'document' | 'reference';
};
export type WorkspacePage = { uid: string; source: number; index: number; rotation: number };
export type WorkspaceReview = { source: number; total: number; markers: number[]; segments: number[][]; options: Record<string, unknown>; expanded: boolean };
export type WorkspaceState = { version: 1; sources: WorkspaceSource[]; pages: WorkspacePage[]; settings: Record<string, unknown>; blankIds?: string[]; review?: WorkspaceReview };
export type WorkspaceSourceStatus = { index: number; path: string; status: 'ready' | 'missing' | 'changed'; message?: string };
export type WorkspaceLoadResult = { state: WorkspaceState | null; sources: WorkspaceSourceStatus[]; savedAt: string | null };
export interface WorkspaceAPI {
  load(scope: WorkspaceScope): Promise<WorkspaceLoadResult>;
  save(scope: WorkspaceScope, state: WorkspaceState): Promise<{ savedAt: string }>;
  clear(scope: WorkspaceScope): Promise<void>;
  flush(): Promise<void>;
  relocate(scope: WorkspaceScope, sourceIndex: number): Promise<WorkspaceLoadResult | null>;
  onFlushRequested(callback: (token: string) => void): () => void;
  flushed(token: string, error?: string): void;
}
