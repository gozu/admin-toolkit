import { useCallback, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { useModal } from '../../hooks/useModal';
import { PluginsTable } from '../PluginsTable';
import { PluginProjectsModal } from '../PluginProjectsModal';
import type { PluginInfo } from '../../types';

export function InstalledPluginsPage() {
  const { state } = useDiag();
  const { parsedData } = state;
  const hasPlugins = (parsedData.plugins?.length ?? 0) > 0;
  const pluginUsageModal = useModal();
  const [usagePlugin, setUsagePlugin] = useState<PluginInfo | null>(null);
  const handleOpenPluginUsage = useCallback((plugin: PluginInfo) => {
    setUsagePlugin(plugin);
    pluginUsageModal.open();
  }, [pluginUsageModal]);

  return (
    <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-4">
      {hasPlugins ? (
        <PluginsTable onOpenUsage={handleOpenPluginUsage} />
      ) : (
        <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-8 text-center">
          <p className="text-[var(--text-secondary)]">No installed plugins detected.</p>
        </div>
      )}
      <PluginProjectsModal
        isOpen={pluginUsageModal.isOpen}
        onClose={pluginUsageModal.close}
        plugin={usagePlugin}
      />
    </div>
  );
}
