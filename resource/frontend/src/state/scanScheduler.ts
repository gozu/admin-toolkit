import { beginScanActivity } from './scanActivityStore';
import { subscribeSessionEpoch } from './sessionCache';

interface Job {
  key: string;
  priority: number;
  cheap: boolean;
  controller: AbortController;
  run: (signal: AbortSignal, priority: number) => Promise<unknown>;
  promise: Promise<void>;
  settle: () => void;
  lane: 'cheap' | 'foreground' | 'background' | null;
}

// Reserve a foreground slot so a long background scan cannot block navigation.
export function createScanScheduler() {
  const jobs = new Map<string, Job>();
  let enabled = false;
  function drain() {
    for (const job of [...jobs.values()].sort((a, b) => a.priority - b.priority)) {
      if (job.lane || (!enabled && job.priority > 0)) continue;
      const lane = job.cheap ? 'cheap' : job.priority === 0 ? 'foreground' : 'background';
      const running = [...jobs.values()].filter((other) => other.lane === lane);
      if (running.length >= (job.cheap ? 2 : 1)) continue;
      job.lane = lane;
      void Promise.resolve().then(() => {
        if (!job.controller.signal.aborted) return job.run(job.controller.signal, job.priority);
      }).catch(() => { /* runners publish their own errors */ }).finally(() => {
        if (jobs.get(job.key) === job) jobs.delete(job.key);
        job.settle();
        drain();
      });
    }
  }
  return {
    enqueue(key: string, priority: number, cheap: boolean, run: Job['run']) {
      const existing = jobs.get(key);
      if (existing) {
        existing.priority = Math.min(existing.priority, priority);
        queueMicrotask(drain);
        return existing.promise;
      }
      let settle!: () => void;
      const finish = beginScanActivity();
      const promise = new Promise<void>((resolve) => { settle = () => { finish(); resolve(); }; });
      jobs.set(key, { key, priority, cheap, run, promise, settle, lane: null, controller: new AbortController() });
      queueMicrotask(drain);
      return promise;
    },
    cancel(key: string) {
      const job = jobs.get(key);
      if (!job) return;
      job.controller.abort();
      // Running jobs retain their slot until cancellation actually settles.
      if (!job.lane) { jobs.delete(key); job.settle(); }
    },
    enable(value: boolean) { enabled = value; if (value) drain(); },
    reset() {
      enabled = false;
      for (const job of jobs.values()) { job.controller.abort(); job.settle(); }
      jobs.clear();
    },
  };
}
export const scanScheduler = createScanScheduler();
subscribeSessionEpoch(() => scanScheduler.reset());
