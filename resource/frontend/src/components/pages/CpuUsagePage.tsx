import { ProcessUsageTable } from '../ProcessUsageTable';

export function CpuUsagePage() {
  return (
    <div className="page-fill gap-6">
      <ProcessUsageTable variant="cpu" />
    </div>
  );
}
