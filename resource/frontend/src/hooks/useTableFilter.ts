import { useCallback, useMemo, useState } from 'react';
import { useDiag } from '../context/DiagContext';

interface FilterOption {
  id: string;
  label: string;
}

interface FilterGroup {
  id: string;
  label: string;
  items: FilterOption[];
}

// Filter group definitions
const FILTER_GROUP_DEFINITIONS = {
  issues: {
    id: 'issues',
    label: 'Issues',
    filterIds: ['disabledFeatures-table'],
  },
  charts: {
    id: 'charts',
    label: 'Charts',
    filterIds: ['filesystem-table', 'memory-chart', 'connections-chart'],
  },
  data: {
    id: 'data',
    label: 'Data',
    filterIds: ['projects-table', 'project-footprint-table', 'plugins-table', 'code-envs-table'],
  },
  system: {
    id: 'system',
    label: 'System',
    filterIds: ['userStats-table', 'systemLimits-table', 'usersByProjects-table'],
  },
  config: {
    id: 'config',
    label: 'Config',
    filterIds: [
      'sparkSettings-table',
      'authSettings-table',
      'containerSettings-table',
      'cgroupSettings-table',
      'resourceLimits-table',
      'javaMemoryLimits-table',
      'enabledSettings-table',
      'maxRunningActivities-table',
      'integrationSettings-table',
      'proxySettings-table',
    ],
  },
};

export function useTableFilter() {
  const { state, setActiveFilter } = useDiag();
  const { activeFilter, parsedData } = state;
  const [searchQuery, setSearchQuery] = useState('');

  // Generate filter options based on available data
  const filterOptions: FilterOption[] = useMemo(() => {
    const options: FilterOption[] = [];

    if (parsedData.disabledFeatures && Object.keys(parsedData.disabledFeatures).length > 0) {
      options.push({ id: 'disabledFeatures-table', label: 'Disabled Features' });
    }

    if (parsedData.userStats && Object.keys(parsedData.userStats).length > 0) {
      options.push({ id: 'userStats-table', label: 'User Statistics' });
    }

    if (parsedData.connections && Object.keys(parsedData.connections).length > 0) {
      options.push({ id: 'connections-chart', label: 'Connection Types' });
    }

    if (parsedData.projects && parsedData.projects.length > 0) {
      options.push({ id: 'projects-table', label: 'Projects' });
    }

    if (parsedData.projectFootprint && parsedData.projectFootprint.length > 0) {
      options.push({ id: 'project-footprint-table', label: 'Project Footprint' });
    }

    if (parsedData.plugins && parsedData.plugins.length > 0) {
      options.push({ id: 'plugins-table', label: 'Plugins' });
    }

    if (parsedData.codeEnvs && parsedData.codeEnvs.length > 0) {
      options.push({ id: 'code-envs-table', label: 'Code Envs' });
    }

    if (parsedData.filesystemInfo && parsedData.filesystemInfo.length > 0) {
      options.push({ id: 'filesystem-table', label: 'Filesystem Usage' });
    }

    if (parsedData.memoryInfo && Object.keys(parsedData.memoryInfo).length > 0) {
      options.push({ id: 'memory-chart', label: 'System Memory' });
    }

    if (parsedData.sparkSettings && Object.keys(parsedData.sparkSettings).length > 0) {
      options.push({ id: 'sparkSettings-table', label: 'Spark Settings' });
    }

    if (parsedData.authSettings && Object.keys(parsedData.authSettings).length > 0) {
      options.push({ id: 'authSettings-table', label: 'Authentication' });
    }

    if (parsedData.containerSettings && Object.keys(parsedData.containerSettings).length > 0) {
      options.push({ id: 'containerSettings-table', label: 'Container Settings' });
    }

    if (parsedData.cgroupSettings && Object.keys(parsedData.cgroupSettings).length > 0) {
      options.push({ id: 'cgroupSettings-table', label: 'CGroup Settings' });
    }

    if (parsedData.resourceLimits && Object.keys(parsedData.resourceLimits).length > 0) {
      options.push({ id: 'resourceLimits-table', label: 'Resource Limits' });
    }

    if (parsedData.javaMemoryLimits && Object.keys(parsedData.javaMemoryLimits).length > 0) {
      options.push({ id: 'javaMemoryLimits-table', label: 'Java Memory Limits' });
    }

    if (parsedData.systemLimits && Object.keys(parsedData.systemLimits).length > 0) {
      options.push({ id: 'systemLimits-table', label: 'System Limits' });
    }

    if (parsedData.enabledSettings && Object.keys(parsedData.enabledSettings).length > 0) {
      options.push({ id: 'enabledSettings-table', label: 'Enabled Settings' });
    }

    if (parsedData.maxRunningActivities && Object.keys(parsedData.maxRunningActivities).length > 0) {
      options.push({ id: 'maxRunningActivities-table', label: 'Max Running Activities' });
    }

    if (parsedData.integrationSettings && Object.keys(parsedData.integrationSettings).length > 0) {
      options.push({ id: 'integrationSettings-table', label: 'Integration Settings' });
    }

    if (parsedData.proxySettings && Object.keys(parsedData.proxySettings).length > 0) {
      options.push({ id: 'proxySettings-table', label: 'Proxy Config' });
    }

    if (parsedData.usersByProjects && Object.keys(parsedData.usersByProjects).length > 0) {
      options.push({ id: 'usersByProjects-table', label: 'Number of Projects per User' });
    }

    if (parsedData.formattedLogErrors && parsedData.formattedLogErrors !== 'No log errors found') {
      options.push({ id: 'log-errors-section', label: 'Log Errors' });
    }

    return options;
  }, [parsedData]);

  // Generate filter groups with counts
  const filterGroups: FilterGroup[] = useMemo(() => {
    const groups: FilterGroup[] = [];
    const availableFilterIds = new Set(filterOptions.map((f) => f.id));

    Object.values(FILTER_GROUP_DEFINITIONS).forEach((groupDef) => {
      const items = groupDef.filterIds
        .filter((id) => availableFilterIds.has(id))
        .map((id) => filterOptions.find((f) => f.id === id)!)
        .filter(Boolean);

      if (items.length > 0) {
        groups.push({
          id: groupDef.id,
          label: groupDef.label,
          items,
        });
      }
    });

    return groups;
  }, [filterOptions]);

  // Get counts for quick filters
  const filterCounts = useMemo(() => {
    const counts: Record<string, number> = {};

    // Issues count
    const disabledCount = parsedData.disabledFeatures
      ? Object.keys(parsedData.disabledFeatures).length
      : 0;
    const errorCount = parsedData.logStats?.['Unique Errors'] || 0;
    counts.issues = disabledCount;
    counts.disabledFeatures = disabledCount;
    counts.logErrors = errorCount;

    // Data counts
    counts.projects = parsedData.projects?.length || 0;
    counts.plugins = parsedData.plugins?.length || 0;
    counts.codeEnvs = parsedData.codeEnvs?.length || 0;
    return counts;
  }, [parsedData]);

  const isVisible = useCallback(
    (tableId: string): boolean => {
      // Search filter takes priority
      if (searchQuery) {
        const option = filterOptions.find((f) => f.id === tableId);
        if (option && !option.label.toLowerCase().includes(searchQuery.toLowerCase())) {
          return false;
        }
      }

      if (activeFilter === 'all') return true;

      // Check if filter is a group
      const groupDef = Object.values(FILTER_GROUP_DEFINITIONS).find(
        (g) => g.id === activeFilter
      );
      if (groupDef) {
        return groupDef.filterIds.includes(tableId);
      }

      return tableId === activeFilter;
    },
    [activeFilter, searchQuery, filterOptions]
  );

  const getFilterLabel = useCallback(
    (filterId: string): string => {
      if (filterId === 'all') return 'All';

      const groupDef = Object.values(FILTER_GROUP_DEFINITIONS).find(
        (g) => g.id === filterId
      );
      if (groupDef) return groupDef.label;

      const option = filterOptions.find((f) => f.id === filterId);
      return option?.label || filterId;
    },
    [filterOptions]
  );

  return {
    filterOptions,
    filterGroups,
    filterCounts,
    activeFilter,
    setActiveFilter,
    isVisible,
    getFilterLabel,
    searchQuery,
    setSearchQuery,
  };
}
