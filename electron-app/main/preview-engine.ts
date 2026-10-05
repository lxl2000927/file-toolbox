// A separate long-lived worker keeps thumbnail reads independent of OCR's
// native document lock. This class owns only preview jobs, never global state.
type Worker = {
  call(method: string, params?: any): Promise<any>;
  shutdown(): Promise<void>;
  addNotificationHandler(handler: (method: string, params: any) => void): unknown;
  addExitHandler(handler: (error: Error) => void): unknown;
};
export class PreviewEngine {
  private worker: Worker | null = null;
  private starting: Promise<Worker> | null = null;
  private jobs = new Set<string>();
  private generation = 0;
  constructor(private create: () => Promise<Worker>, private notify: (method: string, params: any) => void) {}

  owns(id: string): boolean { return this.jobs.has(id); }
  private ready(): Promise<Worker> {
    if (this.worker) return Promise.resolve(this.worker);
    if (this.starting) return this.starting;
    const generation = this.generation;
    const attempt = this.create().then(async worker => {
      if (generation !== this.generation) { await worker.shutdown(); throw new Error('预览引擎已关闭'); }
      this.worker = worker;
      worker.addNotificationHandler((method, params) => {
        if (this.worker !== worker || method === 'engine.status') return;
        if (method === 'task.complete') this.jobs.delete(params?.task_id);
        this.notify(method, params);
      });
      worker.addExitHandler(error => {
        if (this.worker !== worker) return;
        this.worker = null;
        this.failJobs(error.message);
      });
      return worker;
    });
    this.starting = attempt;
    void attempt.finally(() => { if (this.starting === attempt) this.starting = null; }).catch(() => {});
    return attempt;
  }
  private failJobs(error: string) {
    const jobs = [...this.jobs]; this.jobs.clear();
    for (const task_id of jobs) this.notify('task.complete', { task_id, ok: false, error });
  }
  async call(method: string, params: any) {
    const id = typeof params?.task_id === 'string' ? params.task_id : '';
    if (id) this.jobs.add(id);
    try { return await (await this.ready()).call(method, params); }
    catch (error) { if (id) this.jobs.delete(id); throw error; }
  }
  async cancel(id: string) {
    if (!this.jobs.has(id)) return { task_id: id, cancelled: false };
    const worker = await this.ready();
    return worker.call('task.cancel', { task_id: id });
  }
  async shutdown() {
    this.generation++;
    const worker = this.worker, starting = this.starting;
    this.worker = null; this.starting = null;
    this.failJobs('预览引擎已关闭');
    await Promise.allSettled([worker?.shutdown(), starting]);
  }
}
