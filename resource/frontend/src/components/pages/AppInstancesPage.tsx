import { useEffect, useMemo, useState } from 'react';
import { appInstancesScan, setKeepInstance } from '../../state/appInstancesStore';
import { useRedState } from '../../state/redUnlockStore';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { dssUrls } from '../../utils/codeEnvUsageLinks';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { AppInstanceRow, AppRecipeRow, AppTemplateRow } from '../../types';

/** A template row joined with the recipes that call it and the instances it
 *  produced. Instances are matched on `generatingAppId`, which the project
 *  listing carries; the recipe edge needs the macro's creator id. */
interface TemplateGroup {
  app: AppTemplateRow;
  recipes: AppRecipeRow[];
  instances: AppInstanceRow[];
  keepOn: number;
  orphans: number;
  /** True when DSS lists no such app — the template was deleted underneath its instances. */
  missing: boolean;
  /** Is this app reachable as an `App_<appId>` recipe at all? `useAsRecipe`
   *  comes from the app manifest; a template deleted out from under its callers
   *  no longer reports it, so a found App_ recipe also counts as proof.
   *  Everything keepInstance-related is meaningless when this is false. */
  isRecipeApp: boolean;
}

function fmtRelative(ms: number | null, nowMs: number): string {
  if (!ms) return '—';
  if (!nowMs) return new Date(ms).toLocaleDateString();
  const days = Math.floor((nowMs - ms) / 86_400_000);
  if (days <= 0) return 'today';
  if (days === 1) return '1 d ago';
  if (days < 30) return `${days} d ago`;
  if (days < 365) return `${Math.floor(days / 30)} mo ago`;
  return `${(days / 365).toFixed(1)} y ago`;
}

export function AppInstancesPage() {
  const { data, loading, error, scanStarted, scanPhase, scanMessage, finishedAt } =
    appInstancesScan.use();
  const { authed } = useRedState();
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<string>>(new Set());
  const [busyRecipe, setBusyRecipe] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!scanStarted) void appInstancesScan.load();
  }, [scanStarted]);

  const lifecycle = appInstancesScan.lifecycle();
  const complete = scanPhase === 'complete' && !!data;
  const aborted = scanPhase === 'aborted' && !loading;
  // Reference "now" for relative dates: the scan's own finish timestamp, never
  // the wall clock (the React Compiler purity rule bans Date.now() in render).
  const nowMs = finishedAt ? Date.parse(finishedAt) : 0;

  const groups = useMemo<TemplateGroup[]>(() => {
    if (!data) return [];
    const byApp = new Map<string, TemplateGroup>();
    for (const app of data.apps) {
      byApp.set(app.appId, {
        app,
        recipes: [],
        instances: [],
        keepOn: 0,
        orphans: 0,
        missing: false,
        isRecipeApp: false,
      });
    }
    // An instance can outlive its template. Synthesise a row for those app ids
    // so the projects they occupy are never invisible on this page.
    const ensure = (appId: string): TemplateGroup => {
      let group = byApp.get(appId);
      if (!group) {
        group = {
          app: {
            appId,
            label: appId,
            origin: '',
            originProjectKey: null,
            useAsRecipe: false,
            instanceCount: 0,
            lastInstantiation: null,
            instanceOwners: [],
          },
          recipes: [],
          instances: [],
          keepOn: 0,
          orphans: 0,
          missing: true,
          isRecipeApp: false,
        };
        byApp.set(appId, group);
      }
      return group;
    };

    for (const recipe of data.recipes) {
      const group = ensure(recipe.appId);
      group.recipes.push(recipe);
      if (recipe.keepInstance === true) group.keepOn += 1;
    }
    for (const instance of data.instances) {
      const group = ensure(instance.generatingAppId || '(unknown)');
      group.instances.push(instance);
      if (instance.orphan === true) group.orphans += 1;
    }
    return [...byApp.values()]
      .filter((group) => group.instances.length > 0 || group.recipes.length > 0)
      .map((group) => ({
        ...group,
        isRecipeApp: group.app.useAsRecipe || group.recipes.length > 0,
      }));
  }, [data]);

  const rescan = () => {
    setExpandedKeys(new Set());
    setActionError(null);
    void appInstancesScan.load(true);
  };

  const toggleExpanded = (appId: string) => {
    const next = new Set(expandedKeys);
    if (!next.delete(appId)) next.add(appId);
    setExpandedKeys(next);
  };

  const flip = async (recipe: AppRecipeRow, keep: boolean) => {
    setBusyRecipe(recipe.fullId);
    setActionError(null);
    try {
      await setKeepInstance(recipe, keep);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyRecipe(null);
    }
  };

  const totalInstances = data?.instances.length ?? 0;
  const keepOnTotal = data?.recipes.filter((r) => r.keepInstance === true).length ?? 0;
  const attribution = data?.attribution;
  // Instances DSS attributes to an App recipe run. Zero of these on a host that
  // still has instances is the common case, and the reason three columns read
  // n/a — worth stating outright rather than leaving the operator to infer it.
  const recipeCreated = data?.instances.filter((i) => i.creatorFullId).length ?? 0;

  const columns: ColumnDef<TemplateGroup>[] = [
    {
      id: 'template',
      label: 'App template',
      width: '38%',
      sortValue: (group) => group.app.label.toLowerCase(),
      defaultSortDir: 'asc',
      render: (group) => (
        <div className="flex flex-col gap-0.5">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => toggleExpanded(group.app.appId)}
              className="text-left text-sm font-medium text-[var(--text-primary)] hover:text-[var(--neon-cyan)]"
            >
              {expandedKeys.has(group.app.appId) ? '▾' : '▸'} {group.app.label}
            </button>
            {group.missing && <span className="badge badge-critical">template deleted</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-[var(--text-tertiary)]">
            <span>{group.app.appId}</span>
            {group.app.originProjectKey && (
              <a
                href={dssUrls.project(group.app.originProjectKey)}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-[var(--neon-cyan)] hover:underline"
              >
                {group.app.originProjectKey}
              </a>
            )}
          </div>
        </div>
      ),
    },
    {
      id: 'instances',
      label: 'Instances',
      align: 'right',
      mono: true,
      sortValue: (group) => group.instances.length,
      defaultSortDir: 'desc',
      cellClassName: (group) =>
        group.instances.length > 0 ? 'text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]',
      render: (group) => group.instances.length || '—',
    },
    {
      id: 'asRecipe',
      label: 'As recipe',
      align: 'center',
      headerTooltip: 'App carries useAsRecipeSettings — it can be run as an App_<appId> recipe',
      sortValue: (group) => (group.isRecipeApp ? 1 : 0),
      render: (group) =>
        group.isRecipeApp ? (
          <span className="text-[var(--text-secondary)]">yes</span>
        ) : (
          <span className="text-[var(--text-tertiary)]">no</span>
        ),
    },
    {
      id: 'keep',
      label: 'Keep instance',
      align: 'center',
      headerTooltip:
        'App recipes with the Advanced → Keep instance box ticked. Every run of one adds a project permanently.',
      sortValue: (group) => group.keepOn,
      defaultSortDir: 'desc',
      render: (group) => {
        // "n/a" and "0 of 0" are different facts and must not both render as a
        // dash: the first means the flag cannot exist for this app, the second
        // means it can but nothing calls the app from a flow yet.
        if (!group.isRecipeApp) {
          return (
            <span
              className="text-[var(--text-tertiary)]"
              title="Only App-as-recipe apps have a Keep instance setting"
            >
              n/a
            </span>
          );
        }
        return (
          <span
            className={
              group.keepOn > 0
                ? 'font-mono text-xs text-[var(--neon-red)]'
                : 'font-mono text-xs text-[var(--text-secondary)]'
            }
            title={
              group.recipes.length
                ? undefined
                : 'Usable as a recipe, but no flow calls it — nothing to keep instances'
            }
          >
            {group.keepOn} of {group.recipes.length}
          </span>
        );
      },
    },
    {
      id: 'orphans',
      label: 'Orphaned',
      align: 'right',
      mono: true,
      headerTooltip: 'Instances whose creating recipe no longer exists — nothing will ever clean them up',
      sortValue: (group) => group.orphans,
      defaultSortDir: 'desc',
      hidden: () => !attribution?.available,
      cellClassName: (group) =>
        group.orphans > 0 ? 'text-[var(--neon-red)]' : 'text-[var(--text-tertiary)]',
      render: (group) =>
        group.isRecipeApp ? (
          group.orphans
        ) : (
          <span title="Only recipe-created instances can be orphaned">n/a</span>
        ),
    },
    {
      id: 'last',
      label: 'Last instantiated',
      align: 'right',
      sortValue: (group) => group.app.lastInstantiation ?? 0,
      defaultSortDir: 'desc',
      render: (group) => (
        <span className="text-xs text-[var(--text-secondary)]">
          {fmtRelative(group.app.lastInstantiation, nowMs)}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="glass-card space-y-3 p-4">
        <div className="flex flex-wrap items-start gap-3">
          <div>
            <h4 className="text-sm font-semibold text-[var(--text-primary)]">
              App instances &amp; App-as-recipe sprawl
            </h4>
            <p className="mt-0.5 max-w-3xl text-xs text-[var(--text-muted)]">
              Every run of an <code className="font-mono">App_</code> recipe instantiates the app
              into a throwaway project and deletes it when the run succeeds — unless the recipe&apos;s
              Advanced tab has <strong>Keep instance</strong> ticked, in which case every run adds a
              project for good. Instances also survive failed runs, and outlive their recipe when it
              is deleted.
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {loading ? (
              <button
                type="button"
                onClick={() => appInstancesScan.abort()}
                className="rounded border border-[var(--neon-red)]/40 px-3 py-1 text-xs font-mono text-[var(--neon-red)] hover:bg-[var(--neon-red)]/10"
              >
                Abort
              </button>
            ) : (
              <button
                type="button"
                onClick={rescan}
                className="rounded px-3 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
              >
                Rescan
              </button>
            )}
          </div>
        </div>

        {loading && (
          <div>
            <ProgressIndicator lifecycle={lifecycle} />
            <div className="mt-1 font-mono text-xs text-[var(--text-muted)]">{scanMessage}</div>
          </div>
        )}

        {data && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span
              className={totalInstances > 0 ? 'badge badge-warning' : 'badge badge-success'}
            >
              {totalInstances} instance{totalInstances === 1 ? '' : 's'}
            </span>
            <span className={keepOnTotal > 0 ? 'badge badge-critical' : 'badge badge-neutral'}>
              {keepOnTotal} recipe{keepOnTotal === 1 ? '' : 's'} keeping instances
            </span>
            {data.orphans !== null && (
              <span className={data.orphans > 0 ? 'badge badge-critical' : 'badge badge-neutral'}>
                {data.orphans} orphaned
              </span>
            )}
          </div>
        )}

        {complete && data && attribution?.available && totalInstances > 0 && recipeCreated === 0 && (
          <div className="text-xs text-[var(--text-muted)]">
            None of these {totalInstances} instance{totalInstances === 1 ? '' : 's'} was created by
            an App recipe run — they were instantiated from an app&apos;s homepage. That is why{' '}
            <strong>Keep instance</strong> and <strong>Orphaned</strong> read{' '}
            <span className="font-mono">n/a</span>: both only exist for App-as-recipe runs. Project
            growth from manual instantiation is a separate mechanism, and deleting those instances
            is a user decision rather than leftover run debris.
          </div>
        )}

        {attribution && !attribution.available && (
          <div className="text-xs text-[var(--neon-yellow)]">
            Instance → recipe attribution unavailable
            {attribution.error ? `: ${attribution.error}` : ''}. DSS strips the creating recipe from
            every public API response, so this page falls back to app-level counts — orphan
            detection is off rather than guessed.
          </div>
        )}

        {complete && data && data.failedProjects.length > 0 && (
          <div className="text-xs text-[var(--neon-yellow)]">
            {data.failedProjects.length} project
            {data.failedProjects.length === 1 ? '' : 's'} could not be read — the keepInstance and
            orphan counts below are a floor, not a total.
          </div>
        )}

        {aborted && (
          <div className="text-xs text-[var(--neon-yellow)]">
            Scan aborted — showing partial results. Rescan for the full picture.
          </div>
        )}
      </div>

      {error && (
        <div className="glass-card flex items-center gap-3 border border-[var(--neon-red)]/40 p-3 text-sm text-[var(--neon-red)]">
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={rescan}
            className="rounded px-2 py-1 text-xs font-medium text-[var(--accent)] hover:underline"
          >
            Retry
          </button>
        </div>
      )}

      {actionError && (
        <div className="glass-card border border-[var(--neon-red)]/40 p-3 text-sm text-[var(--neon-red)]">
          {actionError}
        </div>
      )}

      {groups.length > 0 ? (
        <DataGrid
          id="app-instances-table"
          title="App templates"
          countBadge={{ total: groups.length }}
          rows={groups}
          columns={columns}
          rowKey={(group) => group.app.appId}
          defaultSortColumnId="instances"
          defaultSortDir="desc"
          scroll="card"
          expandedRowKeys={expandedKeys}
          childRowClassName="bg-[var(--bg-glass)]"
          renderExpandedRow={(group) => (
            <div className="space-y-4 px-4 py-3">
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
                  App recipes ({group.recipes.length})
                </div>
                {group.recipes.length === 0 ? (
                  <div className="text-xs text-[var(--text-muted)]">
                    No <code className="font-mono">App_</code> recipe calls this template — its
                    instances were created from the app homepage, not a flow.
                  </div>
                ) : (
                  <ul className="space-y-1">
                    {group.recipes.map((recipe) => (
                      <li
                        key={recipe.fullId}
                        className="flex flex-wrap items-center gap-2 text-xs"
                      >
                        <a
                          href={dssUrls.recipe(recipe.projectKey, recipe.name)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-mono text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                        >
                          {recipe.fullId}
                        </a>
                        {recipe.keepInstance === true ? (
                          <span className="badge badge-critical">keep instance ON</span>
                        ) : recipe.keepInstance === false ? (
                          <span className="badge badge-neutral">keep instance off</span>
                        ) : (
                          <span className="badge badge-neutral" title={recipe.error || undefined}>
                            unreadable
                          </span>
                        )}
                        {recipe.keepInstance === true && (
                          <button
                            type="button"
                            disabled={!authed || busyRecipe === recipe.fullId}
                            onClick={() => void flip(recipe, false)}
                            title={
                              authed
                                ? 'Set params.keepInstance = false so successful runs delete their instance again'
                                : 'Unlock advanced actions to change this'
                            }
                            className="rounded border border-[var(--accent)]/40 px-2 py-0.5 text-[11px] text-[var(--accent)] hover:bg-[var(--accent)]/10 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            {busyRecipe === recipe.fullId ? 'Saving…' : 'Turn off'}
                          </button>
                        )}
                        {recipe.keepInstance === false && authed && (
                          <button
                            type="button"
                            disabled={busyRecipe === recipe.fullId}
                            onClick={() => void flip(recipe, true)}
                            className="rounded px-2 py-0.5 text-[11px] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] disabled:opacity-40"
                          >
                            {busyRecipe === recipe.fullId ? 'Saving…' : 'turn on'}
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
                  Instance projects ({group.instances.length})
                </div>
                {group.instances.length === 0 ? (
                  <div className="text-xs text-[var(--text-muted)]">No instances on this host.</div>
                ) : (
                  <ul className="space-y-1">
                    {group.instances.map((instance) => (
                      <li
                        key={instance.projectKey}
                        className="flex flex-wrap items-center gap-2 text-xs"
                      >
                        <a
                          href={dssUrls.project(instance.projectKey)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-mono text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                        >
                          {instance.projectKey}
                        </a>
                        {instance.creatorFullId ? (
                          <span className="text-[var(--text-muted)]">
                            from{' '}
                            <a
                              href={dssUrls.recipe(
                                instance.creatorProjectKey || '',
                                instance.creatorRecipeName || '',
                              )}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="font-mono hover:text-[var(--neon-cyan)] hover:underline"
                            >
                              {instance.creatorFullId}
                            </a>
                          </span>
                        ) : attribution?.available ? (
                          <span className="text-[var(--text-muted)]">created from app homepage</span>
                        ) : null}
                        {instance.orphan === true && (
                          <span className="badge badge-critical">orphan — recipe gone</span>
                        )}
                        <span className="ml-auto text-[var(--text-tertiary)]">
                          {fmtRelative(instance.lastModified, nowMs)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        />
      ) : complete && data ? (
        <div className="glass-card p-6 text-center">
          <div className="mb-1 text-2xl text-[var(--neon-green)]">&#10003;</div>
          <div className="text-sm text-[var(--text-secondary)]">
            No app templates or app instances on this host.
          </div>
        </div>
      ) : null}
    </div>
  );
}
