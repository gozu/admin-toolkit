import { useId, useMemo, useState } from 'react';
import type { ParsedData } from '../../../types';
import { dssUrls } from '../../../utils/codeEnvUsageLinks';
import { resolveLifecycleById } from '../../../utils/pageLifecycle';
import { ProgressIndicator } from '../../common/ProgressIndicator';
import { MechanicalChevron, SegmentedControl, SwitchThumb } from '../../common/MechanicalControls';
import { RollingNumber } from '../../common/RollingNumber';
import { buildEstateModel, type EstateKind, type EstateObject } from './estateModel';

const GROUPS: {
  kind: EstateKind;
  label: string;
  page: 'projects' | 'code-envs-cleaner' | 'connections-inventory';
}[] = [
  { kind: 'project', label: 'Projects', page: 'projects' },
  { kind: 'environment', label: 'Environments', page: 'code-envs-cleaner' },
  { kind: 'connection', label: 'Connections', page: 'connections-inventory' },
];
const FILTERS = [
  { value: 'all', label: 'All objects' },
  { value: 'attention', label: 'Needs attention' },
] as const;
const PAGE_SIZE = 96;

function href(object: EstateObject) {
  if (object.kind === 'project') return dssUrls.project(object.key);
  if (object.kind === 'environment') return dssUrls.codeEnv(object.language!, object.key);
  return dssUrls.llmConn(object.key);
}

export function EstateDiscovery({
  data,
  defaultOpen = false,
}: {
  data: ParsedData;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className="estate-discovery relative z-10 mb-2">
      <button
        type="button"
        className="estate-disclosure"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        <MechanicalChevron expanded={open} />
        <span>Estate map</span>
        <span className="text-[var(--text-muted)]">Discover objects and their connections</span>
      </button>
      {open && (
        <div id={id}>
          <EstateMap data={data} />
        </div>
      )}
    </section>
  );
}

function EstateMap({ data }: { data: ParsedData }) {
  const {
    projects,
    projectFootprint,
    codeEnvs,
    connectionDetails,
    connectionHealth,
    connectionAudit,
    connectionDatasetUsages,
    connectionLlmUsages,
  } = data;
  const model = useMemo(
    () =>
      buildEstateModel({
        projects,
        projectFootprint,
        codeEnvs,
        connectionDetails,
        connectionHealth,
        connectionAudit,
        connectionDatasetUsages,
        connectionLlmUsages,
      }),
    [
      projects,
      projectFootprint,
      codeEnvs,
      connectionDetails,
      connectionHealth,
      connectionAudit,
      connectionDatasetUsages,
      connectionLlmUsages,
    ],
  );
  const [filter, setFilter] = useState<'all' | 'attention'>('all');
  const [query, setQuery] = useState('');
  const [illuminate, setIlluminate] = useState(true);
  const [hovered, setHovered] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const activeId = hovered ?? focused ?? pinned;
  const active = model.objects.find((object) => object.id === activeId);
  const neighbors = active ? model.links.get(active.id) : undefined;
  const visible = useMemo(
    () =>
      model.objects.filter(
        (object) =>
          (filter === 'all' || object.attention) &&
          `${object.name} ${object.key} ${object.detail}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [model, filter, query],
  );
  const [pages, setPages] = useState<Record<EstateKind, number>>({
    project: 0,
    environment: 0,
    connection: 0,
  });
  const resetPages = () => setPages({ project: 0, environment: 0, connection: 0 });
  const linksPending = [data.codeEnvsLoading, data.connectionUsageLoading].some(
    (lc) => !lc || lc.phase === 'queued' || lc.phase === 'running',
  );
  return (
    <div
      className="estate-map"
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          setPinned(null);
          setHovered(null);
          setFocused(null);
        }
      }}
    >
      <div className="estate-toolbar">
        <SegmentedControl
          label="Estate objects"
          value={filter}
          options={FILTERS}
          onChange={(value) => {
            setFilter(value);
            resetPages();
          }}
        />
        <input
          aria-label="Find an estate object"
          placeholder="Find an object…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            resetPages();
          }}
        />
        <button
          type="button"
          role="switch"
          aria-checked={illuminate}
          onClick={() => setIlluminate(!illuminate)}
          className="estate-link-toggle"
        >
          <span aria-hidden className="mechanical-switch-track" data-checked={illuminate}>
            <SwitchThumb checked={illuminate} />
          </span>
          Highlight links
        </button>
      </div>
      <div className="estate-groups">
        {GROUPS.map((group) => {
          const objects = visible.filter((object) => object.kind === group.kind);
          const page = Math.min(
            pages[group.kind],
            Math.max(0, Math.ceil(objects.length / PAGE_SIZE) - 1),
          );
          const windowObjects = objects.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
          const lifecycle = resolveLifecycleById(group.page, data);
          return (
            <div key={group.kind} className="estate-group">
              <div className="estate-group-heading">
                <span>{group.label}</span>
                <RollingNumber value={objects.length} />
              </div>
              <div
                className="estate-block-grid"
                aria-label={group.label}
                onKeyDown={(event) => {
                  const delta = (
                    { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 16, ArrowUp: -16 } as Record<
                      string,
                      number
                    >
                  )[event.key];
                  if (delta === undefined && event.key !== 'Home' && event.key !== 'End') return;
                  const buttons = Array.from(event.currentTarget.querySelectorAll('button'));
                  const index = buttons.indexOf(event.target as HTMLButtonElement);
                  if (index < 0) return;
                  event.preventDefault();
                  const next =
                    event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? buttons.length - 1
                        : Math.max(0, Math.min(buttons.length - 1, index + delta));
                  buttons[next]?.focus();
                }}
              >
                {windowObjects.map((object, index) => (
                  <button
                    type="button"
                    key={object.id}
                    className="estate-block"
                    tabIndex={index === 0 ? 0 : -1}
                    aria-label={`${object.name} · ${object.detail}${object.attention ? ' · Needs attention' : ''}`}
                    aria-pressed={pinned === object.id}
                    data-lit={
                      active?.id === object.id ||
                      (illuminate && neighbors?.has(object.id)) ||
                      undefined
                    }
                    data-dim={
                      (illuminate &&
                        active &&
                        active.id !== object.id &&
                        !neighbors?.has(object.id)) ||
                      undefined
                    }
                    title={`${object.name}\n${object.detail}${object.attention ? `\n${object.attention}` : ''}`}
                    onMouseEnter={() => setHovered(object.id)}
                    onMouseLeave={() => setHovered(null)}
                    onFocus={() => setFocused(object.id)}
                    onBlur={() => setFocused(null)}
                    onClick={() => setPinned(pinned === object.id ? null : object.id)}
                  >
                    <span aria-hidden className="estate-block-core" />
                    {object.attention && (
                      <span aria-hidden className="estate-attention">
                        !
                      </span>
                    )}
                  </button>
                ))}
                {lifecycle.phase === 'running' && filter === 'all' && !query && (
                  <span className="estate-frontier" aria-label={`${group.label} scan active`}>
                    <span />
                  </span>
                )}
              </div>
              <div className="estate-group-footer">
                <span>
                  {objects.length
                    ? `${page * PAGE_SIZE + 1}–${page * PAGE_SIZE + windowObjects.length} of ${objects.length}`
                    : query || filter !== 'all'
                      ? 'No matching objects'
                      : lifecycle.phase === 'done'
                        ? 'No objects found'
                        : 'Awaiting inventory'}
                </span>
                <span className="flex gap-2">
                  <button
                    type="button"
                    aria-label={`Previous ${group.label.toLowerCase()}`}
                    disabled={page === 0}
                    onClick={() => setPages({ ...pages, [group.kind]: page - 1 })}
                  >
                    ‹
                  </button>
                  <button
                    type="button"
                    aria-label={`Next ${group.label.toLowerCase()}`}
                    disabled={(page + 1) * PAGE_SIZE >= objects.length}
                    onClick={() => setPages({ ...pages, [group.kind]: page + 1 })}
                  >
                    ›
                  </button>
                </span>
              </div>
              <ProgressIndicator lifecycle={lifecycle} compact />
            </div>
          );
        })}
      </div>
      <div className="estate-inspector">
        {active ? (
          <>
            <div className="min-w-0">
              <strong className="block truncate">{active.name}</strong>
              <span className="block truncate" title={active.attention || active.detail}>
                {active.attention || active.detail} · {neighbors?.size ?? 0} known links
              </span>
            </div>
            <a href={href(active)} target="_blank" rel="noopener noreferrer">
              Open in DSS ↗
            </a>
            {pinned && (
              <button
                type="button"
                onClick={() => {
                  setPinned(null);
                  setHovered(null);
                  setFocused(null);
                }}
                aria-label="Clear inspected object"
              >
                ×
              </button>
            )}
          </>
        ) : (
          <span>Hover or focus to inspect. Click a block to pin it and open its object.</span>
        )}
      </div>
      <p className="estate-legend">
        <span>■ Discovered</span>
        <span>! Needs attention</span>
        <span>
          {linksPending
            ? 'Dependency discovery in progress · known links only'
            : 'Recorded dependencies only'}
        </span>
        <span>Moving sweep = discovery frontier</span>
      </p>
    </div>
  );
}
