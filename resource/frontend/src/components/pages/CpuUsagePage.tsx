import { ProcessUsageTable } from '../ProcessUsageTable';

export function CpuUsagePage() {
  return (
    <div className="w-full py-4 space-y-6">
      <ProcessUsageTable variant="cpu" />
    </div>
  );
}
