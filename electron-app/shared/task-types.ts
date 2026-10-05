export type TaskRecord = {
  task_id: string; method: string; state: string; phase: string; current: number; total: number;
  file: string; error: string; output_files: string[]; created_at: string | number; updated_at: string | number;
  can_retry: boolean;
  output_count?: number;
};
export interface TaskCenterAPI {
  list: () => Promise<{ tasks: TaskRecord[]; storage_error?: string }>;
  pause: (id: string) => Promise<{ task_id: string; state: string; paused: boolean }>;
  resume: (id: string) => Promise<{ task_id: string; state: string; resumed: boolean }>;
  retry: (id: string) => Promise<{ task_id: string; queued?: boolean; position?: number }>;
  reveal: (id: string) => Promise<void>;
}
