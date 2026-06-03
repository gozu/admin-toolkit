import { Fragment, useEffect, useMemo, useState } from 'react';
import { useDiag } from '../context/DiagContext';
import { Modal } from './Modal';
import { useModal } from '../hooks/useModal';
import { useConnectionUsageScan } from '../hooks/useConnectionUsageScan';
import { ScanIncompleteNotice } from './ScanIncompleteNotice';
import { DataGrid } from './common/DataGrid';
import { fetchJson } from '../utils/api';
import type { ColumnDef } from '../utils/dataGridTypes';
import type {
  CodeEnvUsageRef,
  ConnectionLocalFilesystemUsage,
  EmailPreviewItem,
  EmailPreviewResponse,
  EmailSendResponse,
  EmailTemplate,
  OutreachRecipient,
} from '../types';

const LOCAL_FS_CAMPAIGN = 'project';
const LOCAL_FS_TEMPLATE: EmailTemplate = {
  subject: '[DSS Health] Please migrate local filesystem datasets and folders',
  body: [
    'Hi {{owner}},',
    '',
    'Some datasets or managed folders in your DSS projects are stored on the local filesystem. Please migrate these assets to S3 folders, S3 files, or Snowflake tables where appropriate.',
    '',
    'If the data is tabular, such as a CSV or Excel file, prefer a Snowflake table.',
    '',
    'Impacted projects and objects:',
    '{{objects_list}}',
    '',
    'Thanks.',
  ].join('\n'),
};

type LocalFilesystemProjectGroup = {
  projectKey: string;
  projectName: string;
  objects: ConnectionLocalFilesystemUsage[];
};

type LocalFilesystemOwnerGroup = {
  owner: string;
  ownerEmail: string;
  projectCount: number;
  objectCount: number;
  projects: LocalFilesystemProjectGroup[];
};

export function LocalFilesystemMigrationCard() {
  const { state } = useDiag();
  const { parsedData } = state;
  const { scanning, scanned, total, error, failedProjectCount, scannedProjectCount, scan, abort } =
    useConnectionUsageScan();

  const usages = useMemo(
    () => parsedData.connectionLocalFilesystemUsages || [],
    [parsedData.connectionLocalFilesystemUsages],
  );

  const isLoading = scanning && total !== null && (scanned === null || scanned < total);
  const hasResults = usages.length > 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <section className="glass-card p-4">
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Local Filesystem Migration</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Scans all projects to find datasets and managed folders stored on the local filesystem, and helps reach out to their owners.
        </p>
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={scan}
            disabled={scanning}
            className="px-4 py-1.5 rounded-md text-sm font-medium bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {scanning ? 'Scanning...' : 'Scan Usage'}
          </button>
          {scanning && (
            <button
              onClick={abort}
              className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors"
            >
              Abort
            </button>
          )}
        </div>
      </section>

      {/* Scan incomplete notice (self-hides when no failures) */}
      <ScanIncompleteNotice
        failedProjectCount={failedProjectCount}
        scannedProjectCount={scannedProjectCount}
      />

      {/* Progress */}
      {isLoading && (
        <section className="glass-card p-4">
          <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <span className="inline-block w-4 h-4 border-2 border-[var(--text-tertiary)] border-t-transparent rounded-full animate-spin" />
            {total !== null && scanned !== null
              ? `Scanning projects… ${scanned} / ${total}`
              : 'Discovering projects…'}
          </div>
        </section>
      )}

      {/* Error */}
      {error && (
        <section className="glass-card p-4">
          <div className="text-sm text-[var(--neon-red)]">
            <span className="font-medium">Scan error:</span> {error}
          </div>
        </section>
      )}

      {/* Empty state */}
      {!hasResults && !isLoading && !error && (
        <section className="glass-card p-6 text-center">
          <p className="text-sm text-[var(--text-muted)]">
            Click <span className="font-medium text-[var(--text-secondary)]">Scan Usage</span> to discover datasets and folders stored on the local filesystem.
          </p>
        </section>
      )}

      {/* Outreach panel */}
      {hasResults && !isLoading && (
        <LocalFilesystemOutreachPanel usages={usages} />
      )}
    </div>
  );
}

function LocalFilesystemOutreachPanel({ usages }: { usages: ConnectionLocalFilesystemUsage[] }) {
  const { state } = useDiag();
  const previewModal = useModal();
  const detailModal = useModal();
  const { open: openDetail } = detailModal;
  const [detailOwner, setDetailOwner] = useState<LocalFilesystemOwnerGroup | null>(null);
  const [selectedOwners, setSelectedOwners] = useState<Set<string>>(() => new Set());
  const [previewItems, setPreviewItems] = useState<EmailPreviewItem[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [sendLoading, setSendLoading] = useState(false);
  const [sendResult, setSendResult] = useState<EmailSendResponse | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);

  const groups = useMemo(() => groupLocalFilesystemUsages(usages), [usages]);
  const selectedChannel = state.parsedData.configuredMailChannel
    || state.parsedData.mailChannels?.[0]?.id
    || '';
  const selectedCount = selectedOwners.size;
  const selectedObjectCount = useMemo(
    () => groups
      .filter((group) => selectedOwners.has(group.owner))
      .reduce((sum, group) => sum + group.objectCount, 0),
    [groups, selectedOwners],
  );

  useEffect(() => {
    setSelectedOwners((prev) => {
      const validOwners = new Set(groups.map((group) => group.owner));
      const next = new Set(Array.from(prev).filter((owner) => validOwners.has(owner)));
      if (next.size > 0 || groups.length === 0) return next;
      return new Set(groups.map((group) => group.owner));
    });
  }, [groups]);

  const toggleOwner = (owner: string) => {
    setSelectedOwners((prev) => {
      const next = new Set(prev);
      if (next.has(owner)) next.delete(owner);
      else next.add(owner);
      return next;
    });
  };

  const setAllSelected = (selected: boolean) => {
    setSelectedOwners(selected ? new Set(groups.map((group) => group.owner)) : new Set());
  };

  const openPreview = async (scope: 'selected' | 'all') => {
    const targetGroups = scope === 'all'
      ? groups
      : groups.filter((group) => selectedOwners.has(group.owner));
    if (targetGroups.length === 0) return;

    setPreviewLoading(true);
    setSendResult(null);
    setEmailError(null);
    try {
      const response = await fetchJson<EmailPreviewResponse>('/api/tools/email/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaign: LOCAL_FS_CAMPAIGN,
          template: LOCAL_FS_TEMPLATE,
          recipients: targetGroups.map(localFilesystemGroupToRecipient),
        }),
      });
      setPreviewItems(response.previews);
      previewModal.open();
    } catch (err) {
      setEmailError(err instanceof Error ? err.message : String(err));
    } finally {
      setPreviewLoading(false);
    }
  };

  const sendEmails = async () => {
    if (previewItems.length === 0) return;

    setSendLoading(true);
    setEmailError(null);
    try {
      const response = await fetchJson<EmailSendResponse>('/api/tools/email/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaign: LOCAL_FS_CAMPAIGN,
          channelId: selectedChannel || undefined,
          plainText: false,
          previews: previewItems,
        }),
      });
      setSendResult(response);
    } catch (err) {
      setEmailError(err instanceof Error ? err.message : String(err));
    } finally {
      setSendLoading(false);
    }
  };

  const columns: ColumnDef<LocalFilesystemOwnerGroup>[] = [
    {
      id: 'select',
      label: '',
      headerClassName: 'w-8',
      render: (group) => (
        <input
          type="checkbox"
          checked={selectedOwners.has(group.owner)}
          onChange={() => toggleOwner(group.owner)}
          aria-label={`Select ${group.owner}`}
          className="accent-[var(--accent)]"
        />
      ),
    },
    {
      id: 'owner',
      label: 'Project Owner',
      defaultSortDir: 'asc',
      render: (group) => (
        <div>
          <div className="font-medium text-[var(--text-primary)]">{group.owner}</div>
          <div className="text-xs font-mono text-[var(--text-muted)]">{group.ownerEmail}</div>
        </div>
      ),
      sortValue: (group) => group.owner.toLowerCase(),
    },
    {
      id: 'projectCount',
      label: 'Projects',
      align: 'right',
      mono: true,
      render: (group) => group.projectCount,
      sortValue: (group) => group.projectCount,
    },
    {
      id: 'objectCount',
      label: 'Objects',
      align: 'right',
      mono: true,
      render: (group) => (
        <button
          type="button"
          onClick={() => {
            setDetailOwner(group);
            openDetail();
          }}
          className="font-mono hover:text-[var(--neon-cyan)] hover:underline focus:outline-none"
          title={`Show objects owned by ${group.owner}`}
        >
          {group.objectCount}
        </button>
      ),
      sortValue: (group) => group.objectCount,
    },
  ];

  return (
    <section className="glass-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold text-[var(--neon-yellow)]">Local Filesystem Migration</h4>
          <span className="text-xs font-mono text-[var(--text-muted)]">({groups.length} owners)</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-[var(--text-muted)]">
            {selectedCount} selected / {selectedObjectCount} objects
          </span>
          <button
            onClick={() => setAllSelected(true)}
            className="px-2.5 py-1 rounded-md text-xs font-medium border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] transition-colors"
          >
            Select all
          </button>
          <button
            onClick={() => setAllSelected(false)}
            className="px-2.5 py-1 rounded-md text-xs font-medium border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] transition-colors"
          >
            Clear
          </button>
          <button
            onClick={() => openPreview('selected')}
            disabled={previewLoading || selectedCount === 0}
            className="px-3 py-1 rounded-md text-xs font-medium bg-[var(--bg-glass)] text-[var(--text-primary)] hover:bg-[var(--bg-glass-hover)] transition-colors disabled:opacity-50"
          >
            {previewLoading ? 'Preparing...' : 'Preview selected'}
          </button>
          <button
            onClick={() => openPreview('all')}
            disabled={previewLoading || groups.length === 0}
            className="px-3 py-1 rounded-md text-xs font-medium btn-primary disabled:opacity-50"
          >
            Email all
          </button>
        </div>
      </div>

      {emailError && (
        <div className="mb-3 rounded-md border border-[var(--neon-red)]/40 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--neon-red)]">
          {emailError}
        </div>
      )}

      <DataGrid
        rows={groups}
        columns={columns}
        rowKey={(group) => group.owner}
        defaultSortColumnId="objectCount"
        emptyMessage="No local filesystem objects found."
        scroll={{ maxH: '55vh' }}
        rowClassName={() => '[&>td]:align-top'}
      />

      <Modal
        isOpen={detailModal.isOpen}
        onClose={detailModal.close}
        title={detailOwner ? `${detailOwner.owner} — local filesystem objects` : 'Objects'}
        sizePreset="large"
      >
        {detailOwner && <LocalFilesystemObjectsTable projects={detailOwner.projects} />}
      </Modal>

      <Modal
        isOpen={previewModal.isOpen}
        onClose={previewModal.close}
        title="Local Filesystem Migration Email Preview"
        sizePreset="large"
        footer={
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm text-[var(--text-secondary)]">
              {sendResult
                ? `Sent ${sendResult.sentCount}/${sendResult.requestedCount} via ${sendResult.channelId}`
                : `${previewItems.length} email(s) ready`}
            </div>
            <div className="flex gap-2">
              <button
                onClick={previewModal.close}
                className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
              >
                Close
              </button>
              <button
                onClick={sendEmails}
                disabled={sendLoading || previewItems.length === 0}
                className="px-4 py-1.5 rounded btn-primary disabled:opacity-50"
              >
                {sendLoading ? 'Sending...' : 'Send Emails'}
              </button>
            </div>
          </div>
        }
      >
        <div className="space-y-3 max-h-[58vh] overflow-y-auto pr-1">
          {emailError && (
            <div className="rounded-md border border-[var(--neon-red)]/40 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--neon-red)]">
              {emailError}
            </div>
          )}
          {previewItems.map((item) => {
            const result = sendResult?.results.find(
              (entry) => entry.recipientKey === item.recipientKey,
            );
            return (
              <article
                key={`${item.recipientKey}-${item.to}`}
                className="border border-[var(--border-glass)] rounded p-3 space-y-2 bg-[var(--bg-glass)]"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm text-[var(--text-secondary)]">
                    To: <span className="font-mono">{item.to}</span>
                  </div>
                  {result && (
                    <span
                      className={
                        result.status === 'sent'
                          ? 'text-[var(--neon-green)] text-xs font-mono'
                          : 'text-[var(--neon-red)] text-xs font-mono'
                      }
                    >
                      {result.status === 'sent' ? 'sent' : `error: ${result.error || 'failed'}`}
                    </span>
                  )}
                </div>
                <div className="text-sm text-[var(--text-primary)]">
                  Subject: <span className="font-medium">{item.subject}</span>
                </div>
                <iframe
                  srcDoc={item.body}
                  className="w-full border border-[var(--border-primary)] rounded bg-white"
                  style={{ minHeight: '320px', maxHeight: '600px' }}
                  sandbox="allow-same-origin"
                  title={`Email preview for ${item.owner}`}
                />
              </article>
            );
          })}
        </div>
      </Modal>
    </section>
  );
}

function LocalFilesystemObjectsTable({ projects }: { projects: LocalFilesystemProjectGroup[] }) {
  return (
    <div className="rounded-md border border-[var(--border-glass)] bg-[var(--bg-glass)]">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[var(--text-muted)]">
            <th className="text-left font-normal py-1.5 px-2">Project</th>
            <th className="text-left font-normal py-1.5 px-2">Object</th>
            <th className="text-left font-normal py-1.5 px-2">Connection</th>
            <th className="text-left font-normal py-1.5 px-2">Path</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((project) => (
            <Fragment key={project.projectKey}>
              {project.objects.map((object, index) => (
                <tr key={`${project.projectKey}-${object.objectType}-${object.objectId}-${index}`}>
                  <td className="py-1 px-2 align-top text-[var(--neon-cyan)]">
                    {index === 0 ? (
                      <>
                        <div className="font-mono">{project.projectKey}</div>
                        <div className="text-[10px] text-[var(--text-muted)]">{project.projectName}</div>
                      </>
                    ) : null}
                  </td>
                  <td className="py-1 px-2 align-top">
                    <div className="text-[var(--text-secondary)]">{object.objectName || object.objectId}</div>
                    <div className="text-[10px] text-[var(--text-muted)]">
                      {object.objectType}
                      {object.objectSubtype ? ` - ${object.objectSubtype}` : ''}
                    </div>
                  </td>
                  <td className="py-1 px-2 align-top font-mono text-[var(--text-primary)]">{object.connection}</td>
                  <td className="py-1 px-2 align-top font-mono text-[var(--text-muted)] max-w-[260px] truncate" title={typeof object.path === 'string' ? object.path : ''}>
                    {typeof object.path === 'string' && object.path ? object.path : '-'}
                  </td>
                </tr>
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function groupLocalFilesystemUsages(usages: ConnectionLocalFilesystemUsage[]): LocalFilesystemOwnerGroup[] {
  const owners = new Map<string, {
    ownerEmail: string;
    projects: Map<string, LocalFilesystemProjectGroup>;
  }>();

  for (const usage of usages) {
    const owner = usage.owner || 'Unknown';
    const ownerEmail = usage.ownerEmail || owner;
    let ownerGroup = owners.get(owner);
    if (!ownerGroup) {
      ownerGroup = { ownerEmail, projects: new Map() };
      owners.set(owner, ownerGroup);
    }
    if (!ownerGroup.ownerEmail && ownerEmail) {
      ownerGroup.ownerEmail = ownerEmail;
    }

    let project = ownerGroup.projects.get(usage.projectKey);
    if (!project) {
      project = {
        projectKey: usage.projectKey,
        projectName: usage.projectName || usage.projectKey,
        objects: [],
      };
      ownerGroup.projects.set(usage.projectKey, project);
    }
    project.objects.push(usage);
  }

  return Array.from(owners.entries())
    .map(([owner, ownerGroup]) => {
      const projects = Array.from(ownerGroup.projects.values())
        .map((project) => ({
          ...project,
          objects: [...project.objects].sort((a, b) => {
            const objectSort = `${a.objectType}:${a.objectName || a.objectId}`.localeCompare(
              `${b.objectType}:${b.objectName || b.objectId}`,
            );
            if (objectSort !== 0) return objectSort;
            return (a.connection || '').localeCompare(b.connection || '');
          }),
        }))
        .sort((a, b) => a.projectKey.localeCompare(b.projectKey));
      return {
        owner,
        ownerEmail: ownerGroup.ownerEmail || owner,
        projectCount: projects.length,
        objectCount: projects.reduce((sum, project) => sum + project.objects.length, 0),
        projects,
      };
    })
    .sort((a, b) => {
      if (b.objectCount !== a.objectCount) return b.objectCount - a.objectCount;
      return a.owner.localeCompare(b.owner);
    });
}

function localFilesystemGroupToRecipient(group: LocalFilesystemOwnerGroup): OutreachRecipient {
  const usageDetails: CodeEnvUsageRef[] = [];
  const projectKeys = group.projects.map((project) => project.projectKey);
  const connections = new Set<string>();

  for (const project of group.projects) {
    for (const object of project.objects) {
      const connectionName = object.connection || 'Local filesystem';
      connections.add(connectionName);
      const objectType = object.objectType === 'folder' ? 'MANAGED_FOLDER' : 'DATASET';
      const objectBits = [
        object.objectName || object.objectId,
        object.objectSubtype ? `(${object.objectSubtype})` : '',
        object.path ? `- ${object.path}` : '',
      ].filter(Boolean);
      usageDetails.push({
        projectKey: project.projectKey,
        projectName: project.projectName,
        usageType: objectType,
        objectType,
        objectId: object.objectId,
        objectName: objectBits.join(' '),
        codeEnvName: connectionName,
        codeEnvKey: connectionName,
      });
    }
  }

  return {
    recipientKey: group.owner,
    owner: group.owner,
    email: group.ownerEmail || group.owner,
    projectKeys,
    codeEnvNames: Array.from(connections).sort(),
    usageDetails,
    projectKeyForSend: projectKeys[0] || null,
    projects: group.projects.map((project) => ({
      projectKey: project.projectKey,
      name: project.projectName,
      codeEnvCount: new Set(project.objects.map((object) => object.connection || 'Local filesystem')).size,
      codeEnvNames: Array.from(new Set(project.objects.map((object) => object.connection || 'Local filesystem'))).sort(),
      totalObjects: project.objects.length,
    })),
  };
}
