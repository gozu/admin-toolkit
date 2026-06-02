import { useEffect, useMemo, useState } from 'react';
import { Modal } from './Modal';
import { useModal } from '../hooks/useModal';
import { fetchJson, getBackendUrl } from '../utils/api';
import { csTemplateScan } from '../state/csTemplateStore';

interface MigrateStep {
  name: string;
  status: 'ok' | 'skipped' | 'error';
  error?: string;
  [key: string]: unknown;
}

interface ZoneFiles {
  sourceDir?: string;
  targetDir?: string;
  count?: number;
  totalBytes?: number;
  walked?: Array<{ path: string; bytes: number }>;
  copied?: number;
  copiedBytes?: number;
}

interface MigrateResponse {
  status: 'planned' | 'migrated' | 'error';
  error?: string;
  old?: {
    id: string;
    name: string;
    templateId: string;
    libName: string;
    state: string | null;
    owner: string;
  };
  new?: {
    plannedName?: string;
    plannedTemplateId?: string;
    plannedTemplateLabel?: string;
    id?: string;
    name?: string;
    templateId?: string;
    libName?: string;
  };
  files?: {
    sourceDir?: string;
    targetDir?: string;
    count: number;
    totalBytes: number;
    copied?: number;
    copiedBytes?: number;
    skipped?: Array<{ path: string; reason: string }>;
    errors?: Array<{ path: string; error: string }>;
    resources?: ZoneFiles;
    versioned?: ZoneFiles;
  };
  steps?: MigrateStep[];
  warnings?: string[];
  durationMs?: number;
}

function getDssBaseUrl(): string {
  const backendUrl = getBackendUrl('/');
  const parsed = new URL(backendUrl, window.location.origin);
  return `${parsed.protocol}//${parsed.host}`;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let n = bytes;
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit += 1;
  }
  return `${n.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function StateChip({ state }: { state: string | null | undefined }) {
  if (!state) {
    return <span className="font-mono text-xs text-[var(--text-muted)]">unknown</span>;
  }
  const upper = state.toUpperCase();
  let cls = 'text-[var(--text-muted)]';
  if (upper === 'RUNNING') cls = 'text-[var(--text-primary)]';
  else if (upper === 'STARTING' || upper === 'STOPPING') cls = 'text-[var(--neon-yellow)]';
  else if (upper === 'STOPPED') cls = 'text-[var(--text-secondary)]';
  return <span className={`font-mono text-xs ${cls}`}>{upper}</span>;
}

function MigrateResult({
  result,
  baseUrl,
  projectKey,
}: {
  result: MigrateResponse;
  baseUrl: string;
  projectKey: string;
}) {
  const isError = result.status === 'error';
  const isPlanned = result.status === 'planned';
  const isMigrated = result.status === 'migrated';
  const accent = isError
    ? 'border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10'
    : isPlanned
      ? 'border-[var(--border-glass)] bg-[var(--bg-glass)]'
      : 'border-[var(--neon-cyan)]/30 bg-[var(--neon-cyan)]/10';

  return (
    <div className={`rounded-lg border ${accent} px-3 py-2 text-sm`}>
      <div className="flex items-center justify-between text-xs uppercase tracking-wide text-[var(--text-muted)]">
        <span>{result.status}</span>
        {typeof result.durationMs === 'number' && <span>{result.durationMs} ms</span>}
      </div>
      {isError && (
        <div className="mt-1 text-sm text-[var(--neon-red)]">{result.error || 'Unknown error'}</div>
      )}
      {result.old && (
        <div className="mt-2 text-base text-[var(--text-secondary)]">
          Old:{' '}
          <span className="font-mono text-[var(--text-primary)]">{result.old.name}</span>{' '}
          <span className="text-[var(--text-muted)]">
            (templateId={result.old.templateId}, libName={result.old.libName})
          </span>
        </div>
      )}
      {result.new && (
        <div className="text-base text-[var(--text-secondary)]">
          New:{' '}
          <span className="font-mono text-[var(--text-primary)]">
            {result.new.name || result.new.plannedName || '--'}
          </span>{' '}
          <span className="text-[var(--text-muted)]">
            (templateId={result.new.templateId || result.new.plannedTemplateId || '--'}
            {result.new.libName ? `, libName=${result.new.libName}` : ''})
          </span>
          {isMigrated && result.new.id && (
            <>
              {' '}
              <a
                href={`${baseUrl}/projects/${encodeURIComponent(projectKey)}/code-studios/${encodeURIComponent(result.new.id)}/view`}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--neon-cyan)] hover:underline"
              >
                open in DSS
              </a>
            </>
          )}
        </div>
      )}
      {result.files && (
        <div className="mt-2 text-base text-[var(--text-secondary)]">
          Files: <span className="font-mono">{result.files.count}</span> source files,{' '}
          <span className="font-mono">{formatBytes(result.files.totalBytes)}</span>
          {isMigrated && typeof result.files.copied === 'number' && (
            <>
              {' '}
              -- copied <span className="font-mono">{result.files.copied}</span>
              {typeof result.files.copiedBytes === 'number' && (
                <>
                  {' '}(<span className="font-mono">{formatBytes(result.files.copiedBytes)}</span>)
                </>
              )}
              {result.files.skipped && result.files.skipped.length > 0 && (
                <>, skipped <span className="font-mono">{result.files.skipped.length}</span></>
              )}
              {result.files.errors && result.files.errors.length > 0 && (
                <>
                  , <span className="text-[var(--neon-red)]">errors {result.files.errors.length}</span>
                </>
              )}
            </>
          )}
        </div>
      )}
      {result.files && (result.files.resources || result.files.versioned) && (
        <div className="mt-1 space-y-1">
          {(['resources', 'versioned'] as const).map((zone) => {
            const z = result.files?.[zone];
            if (!z || !z.count) return null;
            return (
              <details key={zone} className="text-base">
                <summary className="cursor-pointer text-[var(--text-muted)]">
                  {zone}: <span className="font-mono text-[var(--text-secondary)]">{z.count}</span>{' '}
                  file{z.count === 1 ? '' : 's'}{' '}
                  <span className="font-mono">{formatBytes(z.totalBytes || 0)}</span>
                </summary>
                {z.walked && z.walked.length > 0 && (
                  <ul className="mt-1 ml-4 max-h-40 overflow-auto text-base font-mono text-[var(--text-secondary)]">
                    {z.walked.slice(0, 200).map((w) => (
                      <li key={w.path}>
                        {w.path}{' '}
                        <span className="text-[var(--text-muted)]">({formatBytes(w.bytes)})</span>
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            );
          })}
        </div>
      )}
      {result.files?.skipped && result.files.skipped.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-[var(--text-muted)]">
            Skipped files ({result.files.skipped.length}) -- present in new template starter
          </summary>
          <ul className="mt-1 max-h-32 overflow-auto text-xs font-mono text-[var(--text-muted)]">
            {result.files.skipped.slice(0, 200).map((s) => (
              <li key={s.path}>{s.path}</li>
            ))}
          </ul>
        </details>
      )}
      {result.files?.errors && result.files.errors.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-[var(--neon-red)]">
            File copy errors ({result.files.errors.length})
          </summary>
          <ul className="mt-1 max-h-32 overflow-auto text-xs font-mono text-[var(--neon-red)]">
            {result.files.errors.slice(0, 100).map((e, idx) => (
              <li key={`${e.path}-${idx}`}>{e.path} -- {e.error}</li>
            ))}
          </ul>
        </details>
      )}
      {result.steps && result.steps.some((s) => s.status === 'error') && (
        <details className="mt-2">
          <summary className="cursor-pointer text-sm text-[var(--neon-red)]">
            Errors in steps ({result.steps.filter((s) => s.status === 'error').length})
          </summary>
          <ul className="mt-1 space-y-0.5 text-sm font-mono text-[var(--neon-red)]">
            {result.steps
              .filter((s) => s.status === 'error')
              .map((step, idx) => (
                <li key={`${step.name}-${idx}`}>
                  {step.name}
                  {step.error ? ` -- ${step.error}` : ''}
                </li>
              ))}
          </ul>
        </details>
      )}
      {result.warnings && result.warnings.length > 0 && (
        <div className="mt-2 text-base text-[var(--neon-yellow)]">
          {result.warnings.map((w, idx) => (
            <div key={idx}>! {w}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CSTemplateReplacement() {
  const { data, loading: listLoading, error: loadError, scanStarted } = csTemplateScan.use();
  const projects = useMemo(() => data?.projects ?? [], [data?.projects]);
  const templates = useMemo(() => data?.templates ?? [], [data?.templates]);

  const [selectedProjectKey, setSelectedProjectKey] = useState('');
  const [selectedCsId, setSelectedCsId] = useState('');
  const [targetTemplateId, setTargetTemplateId] = useState('');
  const [confirmText, setConfirmText] = useState('');
  const [busy, setBusy] = useState(false);
  const [migrateError, setMigrateError] = useState<string | null>(null);
  const [result, setResult] = useState<MigrateResponse | null>(null);
  const confirmModal = useModal();

  const baseUrl = useMemo(() => getDssBaseUrl(), []);

  useEffect(() => {
    if (!scanStarted) {
      void csTemplateScan.load();
    }
  }, [scanStarted]);

  const selectedProject = useMemo(
    () => projects.find((p) => p.projectKey === selectedProjectKey) || null,
    [projects, selectedProjectKey],
  );
  const selectedCs = useMemo(
    () => selectedProject?.codeStudios.find((cs) => cs.id === selectedCsId) || null,
    [selectedProject, selectedCsId],
  );

  useEffect(() => {
    if (!selectedProjectKey && projects.length > 0) {
      const firstWithCS = projects.find((p) => p.codeStudios.length > 0);
      if (firstWithCS) setSelectedProjectKey(firstWithCS.projectKey);
    }
  }, [projects, selectedProjectKey]);

  useEffect(() => {
    if (!selectedProject) return;
    if (!selectedProject.codeStudios.some((cs) => cs.id === selectedCsId)) {
      setSelectedCsId(selectedProject.codeStudios[0]?.id || '');
    }
  }, [selectedProject, selectedCsId]);

  useEffect(() => {
    if (!selectedCs) return;
    const choices = templates.filter((t) => t.id !== selectedCs.templateId);
    if (!choices.some((t) => t.id === targetTemplateId)) {
      setTargetTemplateId(choices[0]?.id || '');
    }
  }, [selectedCs, templates, targetTemplateId]);

  const runMigrate = async (dryRun: boolean) => {
    if (!selectedProjectKey || !selectedCsId || !targetTemplateId) return;
    setBusy(true);
    setMigrateError(null);
    if (dryRun) setResult(null);
    try {
      const res = await fetchJson<MigrateResponse>('/api/cs-template/migrate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          projectKey: selectedProjectKey,
          codeStudioId: selectedCsId,
          newTemplateId: targetTemplateId,
          dryRun,
        }),
      });
      setResult(res);
      if (!dryRun) {
        confirmModal.close();
        setConfirmText('');
      }
    } catch (err) {
      setMigrateError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const targetChoices = useMemo(
    () => (selectedCs ? templates.filter((t) => t.id !== selectedCs.templateId) : []),
    [selectedCs, templates],
  );

  const canPreview = Boolean(
    selectedProjectKey && selectedCsId && targetTemplateId && !busy && selectedCs && selectedCs.templateId !== targetTemplateId,
  );
  const previewOk = Boolean(result && result.status === 'planned');
  const canApply = canPreview && previewOk && confirmText === 'do it';

  const projectChoices = useMemo(
    () => projects.filter((p) => p.codeStudios.length > 0),
    [projects],
  );

  return (
    <div className="w-full py-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4">
        <div>
          <h2 className="text-xl font-semibold text-[var(--text-primary)]">Replace CS Template</h2>
          <p className="text-sm text-[var(--text-muted)]">
            Migrate a code studio to a different template. The original code studio is left untouched;
            a new one is created with the target template and your resource files are copied across.
          </p>
        </div>
        <div className="text-xs text-[var(--text-muted)]">
          {projectChoices.length} project{projectChoices.length === 1 ? '' : 's'} with code studios,{' '}
          {templates.length} template{templates.length === 1 ? '' : 's'}
        </div>
      </div>

      {loadError && (
        <div className="rounded-lg border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--neon-red)]">
          Failed to load code studios: {loadError}
        </div>
      )}

      <div className="rounded-xl p-4 space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm text-[var(--text-secondary)]">
            <span>Project</span>
            <select
              value={selectedProjectKey}
              onChange={(e) => setSelectedProjectKey(e.target.value)}
              disabled={listLoading || projectChoices.length === 0}
              className="min-h-10 rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
            >
              {projectChoices.length === 0 ? (
                <option value="">{listLoading ? 'Loading...' : 'No projects with code studios'}</option>
              ) : (
                projectChoices.map((p) => (
                  <option key={p.projectKey} value={p.projectKey}>
                    {p.projectKey} ({p.codeStudios.length})
                  </option>
                ))
              )}
            </select>
          </label>

          <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm text-[var(--text-secondary)]">
            <span>Code Studio</span>
            <select
              value={selectedCsId}
              onChange={(e) => setSelectedCsId(e.target.value)}
              disabled={!selectedProject || selectedProject.codeStudios.length === 0}
              className="min-h-10 rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
            >
              {!selectedProject || selectedProject.codeStudios.length === 0 ? (
                <option value="">--</option>
              ) : (
                selectedProject.codeStudios.map((cs) => (
                  <option key={cs.id} value={cs.id}>
                    {cs.name} -- {cs.templateLabel}
                  </option>
                ))
              )}
            </select>
          </label>

          <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm text-[var(--text-secondary)]">
            <span>Target template</span>
            <select
              value={targetTemplateId}
              onChange={(e) => setTargetTemplateId(e.target.value)}
              disabled={targetChoices.length === 0}
              className="min-h-10 rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
            >
              {targetChoices.length === 0 ? (
                <option value="">--</option>
              ) : (
                targetChoices.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                    {t.description ? ` -- ${t.description}` : ''}
                  </option>
                ))
              )}
            </select>
          </label>

          <button
            onClick={() => void runMigrate(true)}
            disabled={!canPreview}
            className="min-h-10 rounded bg-[var(--accent)] px-4 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
          >
            {busy && !result ? 'Previewing...' : 'Preview'}
          </button>
          <button
            onClick={() => {
              setConfirmText('');
              confirmModal.open();
            }}
            disabled={!canPreview || !previewOk || busy}
            className="min-h-10 rounded border border-[var(--neon-red)]/40 bg-[var(--neon-red)]/10 px-4 text-sm font-medium text-[var(--neon-red)] hover:bg-[var(--neon-red)]/20 disabled:opacity-50"
          >
            Migrate
          </button>
        </div>

        {selectedCs && (
          <div className="flex flex-wrap items-center gap-4 rounded-md border border-[var(--border-glass)] bg-[var(--bg-glass)] px-3 py-2 text-base text-[var(--text-secondary)]">
            <span>
              Owner: <span className="font-mono text-[var(--text-primary)]">{selectedCs.owner || '--'}</span>
            </span>
            <span>
              libName: <span className="font-mono text-[var(--text-primary)]">{selectedCs.libName || '--'}</span>
            </span>
            <span className="flex items-center gap-1">
              State: <StateChip state={selectedCs.state} />
            </span>
            <span>
              Current template:{' '}
              <span className="font-mono text-[var(--text-primary)]">{selectedCs.templateLabel}</span>
            </span>
            {targetTemplateId && (
              <span>
                Will create:{' '}
                <span className="font-mono text-[var(--text-primary)]">
                  {selectedCs.name}-{targetTemplateId}
                </span>
              </span>
            )}
          </div>
        )}

        {migrateError && (
          <div className="text-sm text-[var(--neon-red)]">{migrateError}</div>
        )}
        {result && (
          <MigrateResult result={result} baseUrl={baseUrl} projectKey={selectedProjectKey} />
        )}
      </div>

      <div className="rounded-xl p-4 text-base text-[var(--text-muted)] space-y-1">
        <div>How it works:</div>
        <ul className="list-disc pl-5 space-y-0.5">
          <li>The original CS is <strong>never modified or deleted</strong> -- a new CS is created with the target template.</li>
          <li>Per-CS resource <em>and</em> versioned files are copied across; starter files in the new CS are <strong>not overwritten</strong>.</li>
          <li>A running source CS is stopped (timeout 120s) before copy.</li>
        </ul>
      </div>

      <Modal isOpen={confirmModal.isOpen} onClose={confirmModal.close} title="Migrate code studio">
        <div className="space-y-4">
          <div className="rounded-lg border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--text-primary)]">
            This will create a new code studio{' '}
            <span className="font-mono">
              {selectedCs?.name}-{targetTemplateId}
            </span>{' '}
            in <span className="font-mono">{selectedProjectKey}</span> and copy resource files from{' '}
            <span className="font-mono">{selectedCs?.name}</span>. Type{' '}
            <span className="font-mono">do it</span> to confirm.
          </div>
          <div className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-glass)] px-3 py-2 text-sm text-[var(--text-secondary)]">
            File ops run via the plugin macro{' '}
            <span className="font-mono text-[var(--text-primary)]">cs-template-copy-files</span>{' '}
            (ships with this plugin; impersonates the{' '}
            <span className="font-mono">dataiku</span> service account; requires global admin).
            The macro is invoked twice -- once for the per-CS{' '}
            <span className="font-mono">resources</span> zone, once for the per-CS{' '}
            <span className="font-mono">versioned</span> zone.
          </div>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="do it"
            className="w-full rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={confirmModal.close}
              className="rounded border border-[var(--border-glass)] px-3 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
            >
              Cancel
            </button>
            <button
              onClick={() => void runMigrate(false)}
              disabled={!canApply}
              className="rounded bg-[var(--neon-red)] px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {busy ? 'Migrating...' : 'Migrate'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
