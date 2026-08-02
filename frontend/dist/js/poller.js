// Polling registry.
//
// Three behaviours matter here:
//   * only the VISIBLE page polls -- switching tabs stops the others
//   * everything pauses while the document is hidden, so a backgrounded tab
//     stops spawning rocm-smi processes
//   * repeated failures back off exponentially to 60s instead of hammering a
//     tool that is missing or wedged
//
// Jitter is applied to each interval so several sources don't align into
// synchronised bursts of subprocess spawns.

const MAX_BACKOFF_MS = 60000;

export class Poller {
  constructor() {
    this.jobs = new Map();
    this.activePage = null;

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.#stopAll();
      else this.#startPage(this.activePage);
    });
  }

  /** register({id, page, intervalMs, run}) -- run() is async and may throw. */
  register(job) {
    this.jobs.set(job.id, { ...job, timer: null, failures: 0 });
  }

  setPage(page) {
    this.#stopAll();
    this.activePage = page;
    if (!document.hidden) this.#startPage(page);
  }

  /** Run every job for the current page immediately (the Refresh button). */
  async refreshNow(force = true) {
    const jobs = [...this.jobs.values()].filter((j) => j.page === this.activePage);
    await Promise.allSettled(jobs.map((j) => this.#tick(j, force)));
  }

  #startPage(page) {
    for (const job of this.jobs.values()) {
      if (job.page !== page) continue;
      this.#tick(job, false);           // fire immediately, then schedule
      this.#schedule(job);
    }
  }

  #stopAll() {
    for (const job of this.jobs.values()) {
      if (job.timer) { clearTimeout(job.timer); job.timer = null; }
    }
  }

  #schedule(job) {
    if (job.timer) clearTimeout(job.timer);

    // Back off after 3 consecutive failures rather than on the first blip.
    const base = job.failures >= 3
      ? Math.min(job.intervalMs * 2 ** (job.failures - 2), MAX_BACKOFF_MS)
      : job.intervalMs;
    const jitter = base * (0.85 + Math.random() * 0.3);

    job.timer = setTimeout(async () => {
      await this.#tick(job, false);
      if (job.page === this.activePage && !document.hidden) this.#schedule(job);
    }, jitter);
  }

  async #tick(job, force) {
    try {
      await job.run({ force });
      job.failures = 0;
    } catch (error) {
      job.failures += 1;
      if (job.onError) job.onError(error, job.failures);
    }
  }
}

export const poller = new Poller();
