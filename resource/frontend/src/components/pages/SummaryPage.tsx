import { useState, useCallback, useMemo } from 'react';
import { useDiag } from '../../context/DiagContext';
import {
  HealthScoreCard,
  HealthIssuesDetectedPanel,
  HealthFactorTogglesPanel,
  InfoPanel,
  FileDownloadButtons,
  FileViewer,
  DisabledFeaturesTable,
  DataTable,
} from '../index';
import { useHealthScore, useModal, useSharedHealthFactors } from '../../hooks';
import type { HealthIssue, ParsedData } from '../../types';

interface RuntimeCard {
  id: string;
  title: string;
  dataKey: keyof ParsedData;
}

function runtimeCardForIssue(issueId: string): RuntimeCard | null {
  if (issueId === 'open-files-low') {
    return { id: 'systemLimits-table', title: 'System Limits', dataKey: 'systemLimits' };
  }
  if (issueId.startsWith('java-memory-')) {
    return { id: 'javaMemoryLimits-table', title: 'Java Memory Settings', dataKey: 'javaMemoryLimits' };
  }
  if (issueId === 'cgroups-disabled' || issueId === 'cgroups-empty-targets') {
    return { id: 'cgroupSettings-table', title: 'CGroups Config', dataKey: 'cgroupSettings' };
  }
  if (issueId === 'spark-version-old') {
    return { id: 'sparkSettings-table', title: 'Spark Settings', dataKey: 'sparkSettings' };
  }
  return null;
}

function pickFlaggedRuntimeCards(issues: HealthIssue[], parsedData: ParsedData): RuntimeCard[] {
  const seen = new Set<string>();
  const cards: RuntimeCard[] = [];
  for (const issue of issues) {
    if (issue.severity !== 'critical' && issue.severity !== 'warning') continue;
    const card = runtimeCardForIssue(issue.id);
    if (!card || seen.has(card.dataKey as string)) continue;
    const data = parsedData[card.dataKey];
    if (!data || typeof data !== 'object' || Object.keys(data).length === 0) continue;
    seen.add(card.dataKey as string);
    cards.push(card);
  }
  return cards;
}

export function SummaryPage() {
  const { state } = useDiag();
  const { parsedData } = state;

  const { healthFactorToggles, toggleHealthFactor } = useSharedHealthFactors();
  const healthScore = useHealthScore(parsedData, healthFactorToggles);

  // Don't reveal a real score while analysis is still streaming in.
  const al = parsedData.analysisLoading;
  const scoreReady = al?.phase === 'done' || al?.phase === 'error';

  const flaggedRuntimeCards = useMemo(
    () => pickFlaggedRuntimeCards(healthScore.issues, parsedData),
    [healthScore.issues, parsedData],
  );

  const hasDisabledFeatures =
    parsedData.disabledFeatures && Object.keys(parsedData.disabledFeatures).length > 0;

  // File viewer state
  const fileViewerModal = useModal();
  const [viewingFile, setViewingFile] = useState<{ name: string; content: string } | null>(null);

  const handleViewFile = useCallback(
    (filename: string, content: string) => {
      setViewingFile({ name: filename, content });
      fileViewerModal.open();
    },
    [fileViewerModal],
  );

  const handleDownloadFile = useCallback((filename: string, content: string) => {
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, []);

  return (
    <div className="w-full py-4">
      <div className="space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-stretch">
          <HealthScoreCard healthScore={healthScore} calculating={!scoreReady} />
          <InfoPanel />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          <HealthIssuesDetectedPanel healthScore={healthScore} />
          <HealthFactorTogglesPanel
            healthFactorToggles={healthFactorToggles}
            onToggleHealthFactor={toggleHealthFactor}
          />
        </div>

        {flaggedRuntimeCards.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
            {flaggedRuntimeCards.map((card) => (
              <DataTable
                key={card.id}
                id={card.id}
                title={card.title}
                data={parsedData[card.dataKey] as Record<string, string | number>}
              />
            ))}
          </div>
        )}

        {hasDisabledFeatures && <DisabledFeaturesTable />}

        <FileDownloadButtons
          onViewFile={handleViewFile}
          onDownloadFile={handleDownloadFile}
        />
      </div>

      <FileViewer
        isOpen={fileViewerModal.isOpen}
        onClose={fileViewerModal.close}
        filename={viewingFile?.name || ''}
        content={viewingFile?.content || ''}
        onDownload={handleDownloadFile}
      />
    </div>
  );
}
