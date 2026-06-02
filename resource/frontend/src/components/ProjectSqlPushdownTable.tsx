import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { Card } from './Card';
import { ScanIncompleteNotice } from './ScanIncompleteNotice';
import { getBackendUrl } from '../utils/api';
import {
  getSqlPushdownScan,
  restartSqlPushdownScan,
  startSqlPushdownScan,
  subscribeSqlPushdownScan,
} from '../state/sqlPushdownScan';
import type {
  SqlPushdownOwnerGroup,
  SqlPushdownProjectFinding,
  SqlPushdownRecipeFinding,
} from '../types';

function OwnerRow({
  group,
  dssBaseUrl,
}: {
  group: SqlPushdownOwnerGroup;
  dssBaseUrl: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-[var(--border-glass)] last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full grid grid-cols-[auto_1fr_auto] items-center gap-3 px-4 py-2 text-left hover:bg-[var(--bg-glass)] transition-colors"
        aria-expanded={open}
      >
        <span className="text-[10px] text-[var(--text-muted)] font-mono w-3">
          {open ? '▼' : '▶'}
        </span>
        <span className="min-w-0">
          <span className="text-[var(--text-primary)] font-medium">
            {group.ownerDisplayName}
          </span>
          <span className="text-xs text-[var(--text-muted)] font-mono ml-2">
            {group.ownerLogin}
          </span>
          {group.ownerEmail && (
            <a
              href={`mailto:${group.ownerEmail}`}
              onClick={(e) => e.stopPropagation()}
              className="text-xs text-[var(--neon-cyan)] hover:underline ml-2"
            >
              {group.ownerEmail}
            </a>
          )}
        </span>
        <span className="px-2 py-0.5 text-xs font-mono rounded-full badge-warning">
          {group.totalRecipes} recipe{group.totalRecipes === 1 ? '' : 's'}
        </span>
      </button>
      {open && (
        <div className="px-4 pb-3 pl-10 space-y-2">
          {group.projects.map((proj) => (
            <ProjectRow
              key={proj.projectKey}
              project={proj}
              dssBaseUrl={dssBaseUrl}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ProjectRow({
  project,
  dssBaseUrl,
}: {
  project: SqlPushdownProjectFinding;
  dssBaseUrl: string;
}) {
  const [open, setOpen] = useState(false);
  const projectUrl = dssBaseUrl ? `${dssBaseUrl}/projects/${project.projectKey}/` : null;
  return (
    <div className="rounded-md">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full grid grid-cols-[auto_1fr_auto] items-center gap-2 px-3 py-1.5 text-left hover:bg-[var(--bg-glass)] transition-colors"
        aria-expanded={open}
      >
        <span className="text-[10px] text-[var(--text-muted)] font-mono w-3">
          {open ? '▼' : '▶'}
        </span>
        <span className="min-w-0 text-sm">
          {projectUrl ? (
            <a
              href={projectUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-[var(--neon-cyan)] hover:underline"
            >
              {project.projectName}
            </a>
          ) : (
            <span className="text-[var(--text-primary)]">{project.projectName}</span>
          )}
          <span className="text-xs text-[var(--text-muted)] font-mono ml-2">
            {project.projectKey}
          </span>
        </span>
        <span className="text-xs font-mono text-[var(--text-muted)]">
          {project.recipes.length}
        </span>
      </button>
      {open && (
        <ul className="px-3 pb-2 pl-8 space-y-1">
          {project.recipes.map((r) => (
            <RecipeLine
              key={`${project.projectKey}-${r.recipeName}`}
              projectKey={project.projectKey}
              recipe={r}
              dssBaseUrl={dssBaseUrl}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function RecipeLine({
  projectKey,
  recipe,
  dssBaseUrl,
}: {
  projectKey: string;
  recipe: SqlPushdownRecipeFinding;
  dssBaseUrl: string;
}) {
  const recipeUrl = dssBaseUrl
    ? `${dssBaseUrl}/projects/${projectKey}/recipes/${recipe.recipeName}/`
    : null;
  return (
    <li className="text-sm">
      <div className="flex items-baseline gap-2">
        <span className="text-[var(--text-muted)]">&bull;</span>
        {recipeUrl ? (
          <a
            href={recipeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--neon-cyan)] font-mono hover:underline"
          >
            {recipe.recipeName}
          </a>
        ) : (
          <span className="text-[var(--text-primary)] font-mono">{recipe.recipeName}</span>
        )}
        <span className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-[var(--bg-glass)] text-[var(--text-secondary)]">
          {recipe.recipeType}
        </span>
      </div>
      <div className="pl-4 text-xs text-[var(--text-muted)]">
        connection:{' '}
        <span className="font-mono text-[var(--text-secondary)]">{recipe.connection}</span>
        <span className="ml-2">
          {recipe.inputs.join(', ')} &rarr; {recipe.outputs.join(', ')}
        </span>
      </div>
    </li>
  );
}

export function ProjectSqlPushdownTable() {
  const scan = useSyncExternalStore(
    subscribeSqlPushdownScan,
    getSqlPushdownScan,
    getSqlPushdownScan,
  );

  useEffect(() => {
    startSqlPushdownScan();
  }, []);

  const dssBaseUrl = useMemo(() => {
    const bUrl = getBackendUrl('/');
    try {
      const u = new URL(bUrl, window.location.origin);
      return `${u.protocol}//${u.host}`;
    } catch {
      return '';
    }
  }, []);

  const progressPct = useMemo(() => {
    if (!scan.total || scan.total <= 0) return 0;
    const scanned = scan.scanned ?? 0;
    return Math.max(0, Math.min(100, Math.round((scanned / scan.total) * 100)));
  }, [scan.total, scan.scanned]);

  const isScanning = scan.status === 'scanning';
  const totalFindings = scan.ownerGroups.reduce((sum, g) => sum + g.totalRecipes, 0);

  return (
    <Card
      id="project-sql-pushdown-audit"
      title="SQL Pushdown Audit — Visual Recipes Running on DSS Engine"
      itemCount={scan.status === 'done' ? totalFindings : undefined}
      collapsible
      defaultOpen
    >
      <div className="px-4 py-3 text-sm text-[var(--text-secondary)] border-b border-[var(--border-glass)]">
        When a visual recipe reads from and writes to datasets on the same SQL
        connection, it should run in-database (engine = SQL) rather than pulling
        warehouse-sized data through the DSS host. Recipes below qualify for
        pushdown but are running on the DSS engine.
      </div>

      {scan.status === 'done' && (
        <ScanIncompleteNotice
          failedProjectCount={scan.failedProjectCount}
          scannedProjectCount={scan.scannedProjectCount}
          className="mx-4 my-3"
        />
      )}

      {isScanning && (
        <div className="px-4 py-3 border-b border-[var(--border-glass)]">
          <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
            <span>
              {scan.total === null
                ? 'Discovering projects…'
                : `Scanning ${scan.scanned ?? 0} / ${scan.total} projects…`}
            </span>
            <span className="font-mono">{progressPct}%</span>
          </div>
          <div className="mt-2 h-2 rounded-full bg-[var(--bg-glass)] overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-[var(--neon-amber)] to-[var(--neon-red)] transition-all duration-300 ease-out"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {scan.status === 'error' && (
        <div className="px-4 py-3 text-sm text-[var(--neon-red)]">
          <span className="font-medium">Scan error:</span> {scan.error}
          <button
            type="button"
            onClick={restartSqlPushdownScan}
            className="ml-3 px-2 py-0.5 text-xs rounded border border-[var(--border-default)] hover:bg-[var(--bg-glass-hover)]"
          >
            Retry
          </button>
        </div>
      )}

      {scan.status === 'done' && scan.ownerGroups.length === 0 && (
        <div className="px-4 py-6 text-sm text-[var(--text-secondary)]">
          All qualifying visual recipes are using SQL pushdown. Nothing to fix.
        </div>
      )}

      {scan.ownerGroups.length > 0 && (
        <div className="card-scroll-body">
          {scan.ownerGroups.map((group) => (
            <OwnerRow
              key={group.ownerLogin}
              group={group}
              dssBaseUrl={dssBaseUrl}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
