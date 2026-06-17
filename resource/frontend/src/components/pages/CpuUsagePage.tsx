import { CpuChart } from '../CpuChart';
import { ProcessUsageTable } from '../ProcessUsageTable';

export function CpuUsagePage() {
  return (
    <div className="page-fill gap-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <CpuChart />
      </div>
      <ProcessUsageTable variant="cpu" />
    </div>
  );
}
