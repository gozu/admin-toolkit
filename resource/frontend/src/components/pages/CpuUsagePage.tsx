import { useState } from 'react';
import { ProcessMetricsTable } from '../ProcessMetricsTable';
import { ProcessUsageByUser } from '../ProcessUsageByUser';

export function CpuUsagePage() {
  const [selectedUser, setSelectedUser] = useState<string | null>(null);

  return (
    <div className="w-full py-4 space-y-6">
      <ProcessUsageByUser
        variant="cpu"
        selectedUser={selectedUser}
        onSelectUser={setSelectedUser}
      />
      <ProcessMetricsTable
        variant="cpu"
        filterUser={selectedUser}
        onClearFilter={() => setSelectedUser(null)}
      />
    </div>
  );
}
