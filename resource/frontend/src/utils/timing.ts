// Performance timing utility for diagnostics

interface TimingEntry {
  label: string;
  duration: number;
}

class PerformanceTimer {
  private startTime: number = 0;
  private marks: Map<string, number> = new Map();
  private entries: TimingEntry[] = [];

  start() {
    this.startTime = performance.now();
    this.marks.clear();
    this.entries = [];
  }

  mark(label: string) {
    this.marks.set(label, performance.now());
  }

  measure(label: string, startMark?: string) {
    const endTime = performance.now();
    const startTime = startMark
      ? this.marks.get(startMark) || this.startTime
      : this.marks.get(label) || this.startTime;

    const duration = endTime - startTime;
    this.entries.push({ label, duration });
    return duration;
  }

  getTotal(): number {
    return performance.now() - this.startTime;
  }

  getEntries(): TimingEntry[] {
    return [...this.entries];
  }

  getSummary(): { total: number; entries: TimingEntry[] } {
    return {
      total: this.getTotal(),
      entries: [...this.entries].sort((a, b) => b.duration - a.duration),
    };
  }

  printSummary() {
    const { total, entries } = this.getSummary();

    // Build output
    const lines: string[] = [];
    lines.push(`total ${total.toFixed(2)}ms`);

    for (const entry of entries) {
      const padding = ' '.repeat(Math.max(0, 24 - entry.label.length));
      lines.push(`${entry.label}${padding}${entry.duration.toFixed(2)}ms`);
    }

    console.log('\n=== Performance Timing ===');
    console.log(lines.join('\n'));
    console.log('==========================\n');

    return { total, entries };
  }
}

// Singleton instance
export const timer = new PerformanceTimer();

export type { TimingEntry };
