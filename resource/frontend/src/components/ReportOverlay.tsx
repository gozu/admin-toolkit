import { useEffect, useCallback, useState, useRef } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { ParsedData } from '../types';
import { filterRealMounts, type ReportData } from '../utils/prepareReportData';
import { useHealthScore } from '../hooks/useHealthScore';
import { useTheme } from '../hooks/useTheme';
import { exportReportAsHtml } from '../utils/exportReport';
import dkulogo from '../assets/dkulogo.png';

/* Editorial annual-report deck. The LLM writes the words (narratives,
   headlines, recommendations); everything visual — charts, stat bands,
   spec sheets — is computed deterministically from parsedData. One CSS
   system (.rpt-* in index.css) serves both this overlay and the exported
   standalone HTML, so keep everything pure DOM + CSS (no JS-driven layout). */

const MINT = '#3EDAB2';
const AMBER = '#EDAB4F';
const RED = '#FF6B5E';

const rv = (n: number) => ({ '--rv': n } as CSSProperties);
const pad = (n: number) => String(n).padStart(2, '0');

interface ReportOverlayProps {
  reportData: ReportData;
  parsedData: ParsedData;
  onClose: () => void;
}

export function ReportOverlay({ reportData, parsedData, onClose }: ReportOverlayProps) {
  const [currentSlide, setCurrentSlide] = useState(0);
  const overlayRef = useRef<HTMLDivElement>(null);
  const { theme } = useTheme();
  const healthScore = useHealthScore(parsedData);
  const slides = reportData.slides;

  // Optional module slides (Compute & Cost) only exist when their
  // data is loaded, so every index after Users & Activity is computed.
  const costTotals = parsedData.projectCostData?.totals;
  let cursor = 12;
  const costIdx = costTotals ? cursor++ : -1;
  const logsIdx = cursor++;
  const recCriticalIdx = cursor++;
  const recImportantIdx = cursor++;
  const recNiceIdx = cursor++;
  const actionIdx = cursor++;
  const closingIdx = cursor++;
  const totalSlides = cursor;

  const next = useCallback(
    () => setCurrentSlide(i => Math.min(i + 1, totalSlides - 1)),
    [totalSlides],
  );
  const prev = useCallback(() => setCurrentSlide(i => Math.max(i - 1, 0)), []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't hijack arrows/space while the user edits deck text inline
      if ((e.target as HTMLElement)?.isContentEditable) return;
      if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); next(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); prev(); }
      else if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [next, prev, onClose]);

  const handleExport = useCallback(() => {
    if (overlayRef.current) {
      exportReportAsHtml(overlayRef.current, parsedData.company || 'unknown', theme);
    }
  }, [parsedData.company, theme]);

  const scoreColor = (n: number | undefined): string | undefined => {
    if (n == null) return undefined;
    return n < 50 ? RED : n < 80 ? AMBER : MINT;
  };

  const company = parsedData.company || 'Unknown Instance';
  const now = new Date();
  const date = now.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  const quarter = `Q${Math.floor(now.getMonth() / 3) + 1}`;
  const quarterLong = `${quarter} ${now.getFullYear()}`;
  const progressPct = ((currentSlide + 1) / totalSlides) * 100;

  // Lean live mode loads no basic project list — fall back to counts the
  // footprint scan measured.
  const totalProjects =
    parsedData.projects?.length ||
    parsedData.projectFootprintSummary?.projectCount ||
    parsedData.projectFootprint?.length ||
    0;

  /* ── Chart data, all derived from parsedData ─────────────────── */

  // Real disk mounts, worst-first (df order starts with tmpfs noise)
  const mountBars: BarRow[] = [...filterRealMounts(parsedData.filesystemInfo)]
    .sort((a, b) => (parseInt(b['Use%']) || 0) - (parseInt(a['Use%']) || 0))
    .slice(0, 5)
    .map(f => {
      const pct = parseInt(f['Use%'] || '') || 0;
      return {
        label: f['Mounted on'] || f.Filesystem || '',
        value: pct,
        display: f.Size ? `${pct}% of ${f.Size}` : `${pct}%`,
        color: pct >= 90 ? RED : pct >= 70 ? AMBER : MINT,
      };
    });

  const pyBars: BarRow[] = Object.entries(parsedData.pythonVersionCounts || {})
    .sort(([, a], [, b]) => b - a)
    .slice(0, 6)
    .map(([v, c]) => ({
      label: `Python ${v}`,
      value: c,
      display: String(c),
      color: /^3\.[0-7]$/.test(v) ? AMBER : MINT,
    }));

  const connBars: BarRow[] = Object.entries(parsedData.connectionCounts || {})
    .sort(([, a], [, b]) => b - a)
    .slice(0, 7)
    .map(([t, c]) => ({ label: t, value: c, display: String(c) }));

  const fpBars: BarRow[] = [...(parsedData.projectFootprint || [])]
    .sort((a, b) => b.totalBytes - a.totalBytes)
    .slice(0, 6)
    .map(p => ({
      label: p.projectKey,
      value: p.totalGB,
      display: `${p.totalGB >= 10 ? Math.round(p.totalGB) : p.totalGB.toFixed(1)} GB`,
      color: p.projectSizeHealth === 'red' ? RED : p.projectSizeHealth === 'orange' ? AMBER : MINT,
    }));

  const profCounts: Record<string, number> = {};
  (parsedData.users || []).forEach(u => {
    const p = u.userProfile || 'UNKNOWN';
    profCounts[p] = (profCounts[p] || 0) + 1;
  });
  const profBars: BarRow[] = Object.entries(profCounts)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5)
    .map(([p, c]) => ({ label: p.replace(/_/g, ' '), value: c, display: String(c) }));

  const costBars: BarRow[] = [...(parsedData.projectCostData?.projects || [])]
    .sort((a, b) => b.memGBh - a.memGBh)
    .slice(0, 6)
    .map(p => ({ label: p.projectKey, value: p.memGBh, display: `${Math.round(p.memGBh)} GB·h` }));

  const li = (parsedData.licenseInfo || {}) as Record<string, unknown>;
  const specRows: SpecRow[] = [
    { k: 'DSS Version', v: parsedData.dssVersion },
    { k: 'Operating System', v: parsedData.osInfo },
    { k: 'CPU Cores', v: parsedData.cpuCores },
    { k: 'Python', v: parsedData.pythonVersion },
    { k: 'Last Restart', v: parsedData.lastRestartTime },
    { k: 'License', v: typeof li.licenseType === 'string' ? li.licenseType : undefined },
    { k: 'License Expires', v: typeof li.expiresOn === 'string' ? li.expiresOn : undefined },
  ].filter((r): r is SpecRow => !!r.v);

  const riskLevel = (slides?.issues?.risk_level || '').toLowerCase();
  const riskColor = riskLevel.includes('high') || riskLevel.includes('critical') ? RED
    : riskLevel.includes('medium') || riskLevel.includes('moderate') ? AMBER : MINT;

  const envScore = healthScore.categories.find(c => c.category === 'code_envs')?.score;
  const fpScore = healthScore.categories.find(c => c.category === 'project_footprint')?.score;

  const meta = { company, date };

  return createPortal(
    <div className="report-overlay" data-theme={theme} ref={overlayRef}>
      <div className="report-slides-container">

        {/* ── Title ───────────────────────────────────────────────── */}
        <div className={`report-slide report-slide-hero${currentSlide === 0 ? ' active' : ''}`} data-slide-index={0}>
          <div className="rpt-ghost rpt-ghost-hero" aria-hidden="true">{quarter}</div>
          <div className="rpt-hero">
            <div className="rpt-hero-top" data-reveal style={rv(0)}>
              <img src={dkulogo} alt="Dataiku" id="dku-logo" className="rpt-hero-logo" />
              <span className="rpt-hero-brand">Dataiku</span>
            </div>
            <div className="rpt-hero-mid">
              <div className="rpt-eyebrow" data-reveal style={rv(1)}>Quarterly Health Check · {quarterLong}</div>
              <h1 className="rpt-hero-title" data-reveal style={rv(2)} contentEditable suppressContentEditableWarning>{company}</h1>
              <div className="rpt-rule" data-reveal style={rv(3)} />
              <div className="rpt-hero-meta" data-reveal style={rv(4)}>
                {[
                  parsedData.dssVersion && `DSS ${parsedData.dssVersion}`,
                  date,
                  totalProjects ? `${totalProjects} projects` : null,
                  parsedData.users?.length ? `${parsedData.users.length} users` : null,
                ].filter(Boolean).join('  ·  ')}
              </div>
            </div>
            <div className="rpt-hero-bottom" data-reveal style={rv(5)}>Prepared by your Dataiku Technical Account Manager</div>
          </div>
        </div>

        {/* ── 01 Executive Summary ────────────────────────────────── */}
        <Slide index={1} active={currentSlide === 1} section="Executive Summary"
          headline={slides?.executive_summary?.headline || 'The quarter in review'} meta={meta}>
          <div className="rpt-exec">
            <div className="rpt-exec-left" data-reveal style={rv(2)}>
              <Gauge score={healthScore.overall} status={healthScore.status} />
              {healthScore.categories.length > 0 && (
                <div className="rpt-cats">
                  {healthScore.categories.map((cat, i) => (
                    <div key={i} className="rpt-cat">
                      <span className="rpt-cat-label">{cat.label}</span>
                      <span className="rpt-cat-track">
                        <span className="rpt-cat-fill" style={{ width: `${Math.max(2, Math.min(100, cat.score))}%`, background: scoreColor(cat.score) }} />
                      </span>
                      <span className="rpt-cat-score" style={{ color: scoreColor(cat.score) }}>{Math.round(cat.score)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="rpt-exec-right">
              <div className="rpt-exec-status" data-reveal style={rv(3)} contentEditable suppressContentEditableWarning>
                {slides?.executive_summary?.overall_status || 'No summary available.'}
              </div>
              <div className="rpt-findings">
                {(slides?.executive_summary?.findings || []).slice(0, 3).map((f, i) => (
                  <div key={i} className="rpt-finding" data-reveal style={rv(4 + i)}>
                    <span className="rpt-finding-num">{pad(i + 1)}</span>
                    <span contentEditable suppressContentEditableWarning>{f}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Slide>

        {/* ── 02 Instance Overview ────────────────────────────────── */}
        <Slide index={2} active={currentSlide === 2} section="The Platform"
          headline={slides?.instance_overview?.headline || 'Instance Overview'} meta={meta}>
          <div className="rpt-split">
            <Narrative text={slides?.instance_overview?.narrative} rvi={2} />
            <Spec rows={specRows} base={3} />
          </div>
        </Slide>

        {/* ── 03 Projects ─────────────────────────────────────────── */}
        <Slide index={3} active={currentSlide === 3} section="The Platform"
          headline={slides?.projects?.headline || 'Projects Overview'} meta={meta}>
          <div className="rpt-split-wide">
            <div data-reveal style={rv(2)}>
              <div className="rpt-bignum" contentEditable suppressContentEditableWarning>{totalProjects || '—'}</div>
              <div className="rpt-bignum-label" contentEditable suppressContentEditableWarning>Projects on this instance</div>
              {fpScore != null && (
                <div className="rpt-bignum-sub" style={{ color: scoreColor(fpScore) }}>
                  Project health {Math.round(fpScore)} / 100
                </div>
              )}
            </div>
            <div>
              <Narrative text={slides?.projects?.narrative} compact rvi={3} />
              {!!slides?.projects?.highlights?.length && <EList items={slides.projects.highlights} base={4} />}
            </div>
          </div>
        </Slide>

        {/* ── 04 Project Footprint ────────────────────────────────── */}
        <Slide index={4} active={currentSlide === 4} section="The Platform"
          headline={slides?.project_footprint?.headline || 'Project Footprint'} meta={meta}>
          <Stats base={2} items={[
            { value: String(parsedData.projectFootprintSummary?.projectCount ?? parsedData.projectFootprint?.length ?? '—'), label: 'Projects Analyzed' },
            { value: parsedData.projectFootprintSummary?.instanceAvgProjectGB != null ? `${parsedData.projectFootprintSummary.instanceAvgProjectGB.toFixed(2)} GB` : '—', label: 'Avg Project Size' },
            ...(fpBars[0] ? [{ value: fpBars[0].display, label: 'Largest Project', color: fpBars[0].color }] : []),
          ]} />
          <div className="rpt-split">
            {fpBars.length > 0 ? <Bars rows={fpBars} base={5} /> : <div />}
            <div>
              <Narrative text={slides?.project_footprint?.narrative} compact rvi={6} />
              {!!slides?.project_footprint?.risks?.length && <Tags items={slides.project_footprint.risks} color={AMBER} base={7} />}
            </div>
          </div>
        </Slide>

        {/* ── 05 Code Environments ────────────────────────────────── */}
        <Slide index={5} active={currentSlide === 5} section="Code Environments"
          headline={slides?.code_envs?.headline || 'Code Environments'} meta={meta}>
          <Stats base={2} items={[
            { value: String(parsedData.codeEnvs?.length ?? '—'), label: 'Total Environments' },
            { value: String(Object.keys(parsedData.pythonVersionCounts || {}).length), label: 'Python Versions' },
            { value: String(Object.keys(parsedData.rVersionCounts || {}).length || '0'), label: 'R Versions' },
          ]} />
          <div className="rpt-split">
            {pyBars.length > 0 ? <Bars rows={pyBars} base={5} /> : <div />}
            <Narrative text={slides?.code_envs?.narrative} compact rvi={6} />
          </div>
        </Slide>

        {/* ── 06 Code Env Health ──────────────────────────────────── */}
        <Slide index={6} active={currentSlide === 6} section="Code Environments"
          headline={slides?.code_env_health?.headline || 'Code Environment Health'} meta={meta}>
          <Stats base={2} items={[
            { value: envScore != null ? String(Math.round(envScore)) : '—', label: 'Env Health Score', color: scoreColor(envScore) },
            { value: String(parsedData.codeEnvs?.filter(e => e.usageCount === 0).length ?? '0'), label: 'Unused Environments' },
          ]} />
          <div className="rpt-split">
            <Narrative text={slides?.code_env_health?.narrative} compact rvi={4} />
            {!!slides?.code_env_health?.upgrade_paths?.length && (
              <EList items={slides.code_env_health.upgrade_paths} mono arrow base={5} />
            )}
          </div>
        </Slide>

        {/* ── 07 Filesystem ───────────────────────────────────────── */}
        <Slide index={7} active={currentSlide === 7} section="Infrastructure"
          headline={slides?.filesystem?.headline || 'Filesystem Health'} meta={meta}>
          <div className="rpt-split">
            {mountBars.length > 0 ? <Bars rows={mountBars} thick base={2} /> : <div />}
            <div>
              <Narrative text={slides?.filesystem?.narrative} compact rvi={3} />
              {!!slides?.filesystem?.warnings?.length && <Tags items={slides.filesystem.warnings} color={RED} base={4} />}
            </div>
          </div>
        </Slide>

        {/* ── 08 Memory & JVM ─────────────────────────────────────── */}
        <Slide index={8} active={currentSlide === 8} section="Infrastructure"
          headline={slides?.memory?.headline || 'Memory & JVM'} meta={meta}>
          <Stats base={2} items={[
            { value: parsedData.javaMemorySettings?.BACKEND || parsedData.javaMemoryLimits?.BACKEND || parsedData.javaMemorySettings?.Xmx || '—', label: 'Backend Heap (Xmx)' },
            { value: parsedData.javaMemorySettings?.JEK || parsedData.javaMemorySettings?.FEK || parsedData.javaMemorySettings?.Xms || '—', label: parsedData.javaMemorySettings?.JEK ? 'JEK Heap (Xmx)' : 'FEK Heap (Xmx)' },
            { value: parsedData.memoryInfo?.total || parsedData.memoryInfo?.['Mem:total'] || '—', label: 'System RAM' },
            { value: parsedData.memoryInfo?.available || parsedData.memoryInfo?.['Mem:available'] || '—', label: 'Available' },
          ]} />
          <div className="rpt-split">
            <Narrative text={slides?.memory?.narrative} compact rvi={6} />
            {!!slides?.memory?.tuning_recs?.length && <EList items={slides.memory.tuning_recs} base={7} />}
          </div>
        </Slide>

        {/* ── 09 Connections ──────────────────────────────────────── */}
        <Slide index={9} active={currentSlide === 9} section="Connectivity"
          headline={slides?.connections?.headline || 'Connections'} meta={meta}>
          <Stats base={2} items={[
            { value: String(parsedData.connectionDetails?.length ?? (Object.values(parsedData.connectionCounts || {}).reduce((a, b) => a + b, 0) || '—')), label: 'Total Connections' },
            { value: String(Object.keys(parsedData.connectionCounts || {}).length), label: 'Connection Types' },
          ]} />
          <div className="rpt-split">
            {connBars.length > 0 ? <Bars rows={connBars} base={4} /> : <div />}
            <Narrative text={slides?.connections?.narrative} compact rvi={5} />
          </div>
        </Slide>

        {/* ── 10 Issues & Risks ───────────────────────────────────── */}
        <Slide index={10} active={currentSlide === 10} section="Governance & Risk"
          headline={slides?.issues?.headline || 'Issues & Risks'} meta={meta}>
          <div className="rpt-split-wide">
            <div data-reveal style={rv(2)}>
              <div className="rpt-risk-label">Assessed risk level</div>
              <div className="rpt-risk-word" style={{ color: riskColor }} contentEditable suppressContentEditableWarning>
                {slides?.issues?.risk_level || 'unknown'}
              </div>
              <div className="rpt-risk-stats">
                {[
                  { value: String(Object.keys(parsedData.disabledFeatures || {}).length), label: 'Disabled Features' },
                  { value: String(parsedData.pluginDetails?.length ?? parsedData.plugins?.length ?? '—'), label: 'Plugins' },
                  { value: String(parsedData.clusters?.length ?? '0'), label: 'Clusters' },
                ].map((s, i) => (
                  <div key={i} className="rpt-ministat">
                    <span className="rpt-ministat-value">{s.value}</span>
                    <span className="rpt-ministat-label">{s.label}</span>
                  </div>
                ))}
              </div>
            </div>
            <Narrative text={slides?.issues?.narrative} rvi={3} />
          </div>
        </Slide>

        {/* ── 11 Users & Activity ─────────────────────────────────── */}
        <Slide index={11} active={currentSlide === 11} section="People"
          headline={slides?.users?.headline || 'Users & Activity'} meta={meta}>
          <Stats base={2} items={[
            { value: String(parsedData.users?.length ?? '—'), label: 'Total Users' },
            { value: String(parsedData.users?.filter(u => u.enabled !== false).length ?? '—'), label: 'Active Users' },
            { value: String(parsedData.users?.filter(u => (u.userProfile || '').includes('DESIGNER') || u.userProfile === 'DATA_SCIENTIST').length ?? '—'), label: 'Designers' },
            { value: String(totalProjects || '—'), label: 'Projects' },
          ]} />
          <div className="rpt-split">
            {profBars.length > 0 ? <Bars rows={profBars} base={6} /> : <div />}
            <Narrative text={slides?.users?.narrative} compact rvi={7} />
          </div>
        </Slide>

        {/* ── Compute & Cost (only when the Cost/CRU module has data) ── */}
        {costTotals && (
          <Slide index={costIdx} active={currentSlide === costIdx} section="Compute & Cost"
            headline={slides?.compute_cost?.headline || 'Compute & Cost'} meta={meta}>
            <Stats base={2} items={[
              { value: String(Math.round(costTotals.memGBh ?? 0)), label: 'Memory GB·h' },
              { value: String(Math.round(costTotals.cpuH ?? 0)), label: 'CPU Hours' },
              { value: costTotals.llmUSD != null ? `$${costTotals.llmUSD >= 100 ? Math.round(costTotals.llmUSD) : costTotals.llmUSD.toFixed(2)}` : '—', label: 'LLM Spend', color: MINT },
              { value: String(costTotals.projectCount ?? '—'), label: 'Projects with Usage' },
            ]} />
            <div className="rpt-split">
              {costBars.length > 0 ? <Bars rows={costBars} base={6} /> : <div />}
              <div>
                <Narrative text={slides?.compute_cost?.narrative} compact rvi={7} />
                {!!slides?.compute_cost?.drivers?.length && <EList items={slides.compute_cost.drivers} base={8} />}
              </div>
            </div>
          </Slide>
        )}

        {/* ── Log Analysis ────────────────────────────────────────── */}
        <Slide index={logsIdx} active={currentSlide === logsIdx} section="Operations"
          headline={slides?.logs?.headline || 'Log Analysis'} meta={meta}>
          <Stats base={2} items={[
            { value: String(parsedData.logStats?.['Unique Errors'] ?? '—'), label: 'Unique Errors' },
            { value: String(parsedData.logStats?.['Total Lines'] ?? '—'), label: 'Total Log Lines' },
            { value: String(parsedData.logStats?.['Displayed Errors'] ?? '—'), label: 'Displayed' },
          ]} />
          <div className="rpt-split">
            <Narrative text={slides?.logs?.narrative} compact rvi={5} />
            {!!slides?.logs?.patterns?.length && (
              <div className="rpt-terminal" data-reveal style={rv(6)}>
                {slides.logs.patterns.slice(0, 5).map((p, i) => (
                  <div key={i} className="rpt-terminal-line" contentEditable suppressContentEditableWarning>{p}</div>
                ))}
              </div>
            )}
          </div>
        </Slide>

        {/* ── Recommendations ─────────────────────────────────────── */}
        <RecSlide index={recCriticalIdx} active={currentSlide === recCriticalIdx} meta={meta}
          section="Recommendations" title="Address these first" tone="critical" toneLabel="Critical"
          items={slides?.rec_critical?.items || []} />
        <RecSlide index={recImportantIdx} active={currentSlide === recImportantIdx} meta={meta}
          section="Recommendations" title="Plan for this quarter" tone="important" toneLabel="Important"
          items={slides?.rec_important?.items || []} />
        <RecSlide index={recNiceIdx} active={currentSlide === recNiceIdx} meta={meta}
          section="Recommendations" title="Worth the polish" tone="nice" toneLabel="Nice to Have"
          items={slides?.rec_nice_to_have?.items || []} />

        {/* ── Action Plan ─────────────────────────────────────────── */}
        <Slide index={actionIdx} active={currentSlide === actionIdx} section="The Road Ahead"
          headline={slides?.action_plan?.headline || 'Action Plan'} meta={meta}>
          <div className="rpt-actions">
            <div className="rpt-actions-head" data-reveal style={rv(2)}>
              <span>№</span><span>Action</span><span className="rpt-right">Timeline</span><span className="rpt-right">Effort</span>
            </div>
            {(slides?.action_plan?.priorities || []).map((p, i) => (
              <div key={i} className="rpt-action" data-reveal style={rv(3 + i)}>
                <span className="rpt-action-num">{pad(i + 1)}</span>
                <span className="rpt-action-text" contentEditable suppressContentEditableWarning>{p.action}</span>
                <span className="rpt-action-when" contentEditable suppressContentEditableWarning>{p.timeline}</span>
                <span className={`rpt-effort rpt-effort-${p.effort || 'medium'}`}><i />{p.effort || 'medium'}</span>
              </div>
            ))}
            {!slides?.action_plan?.priorities?.length && (
              <div className="rpt-empty" data-reveal style={rv(3)}>No action items generated.</div>
            )}
          </div>
        </Slide>

        {/* ── Closing ─────────────────────────────────────────────── */}
        <div className={`report-slide report-slide-hero${currentSlide === closingIdx ? ' active' : ''}`} data-slide-index={closingIdx}>
          <div className="rpt-ghost rpt-ghost-hero" aria-hidden="true">{quarter}</div>
          <div className="rpt-hero">
            <div className="rpt-hero-top" data-reveal style={rv(0)}>
              <img src={dkulogo} alt="Dataiku" id="dku-logo-closing" className="rpt-hero-logo" />
              <span className="rpt-hero-brand">Dataiku</span>
            </div>
            <div className="rpt-hero-mid">
              <div className="rpt-eyebrow" data-reveal style={rv(1)}>Next Steps</div>
              <h1 className="rpt-hero-title rpt-hero-title-sm" data-reveal style={rv(2)} contentEditable suppressContentEditableWarning>
                Thank you.
              </h1>
              <div className="rpt-rule" data-reveal style={rv(3)} />
              <div className="rpt-closing-note" data-reveal style={rv(4)} contentEditable suppressContentEditableWarning>
                Review the recommendations with your team and prioritize based on your operational needs.
                Your Technical Account Manager is available for follow-up discussions.
              </div>
            </div>
            <div className="rpt-hero-bottom" data-reveal style={rv(5)}>{company} · {date}</div>
          </div>
        </div>
      </div>

      {/* ── Navigation ─────────────────────────────────────────── */}
      <div className="report-nav">
        <div className="report-progress-bar" style={{ width: `${progressPct}%` }} />
        <div className="rpt-nav-group">
          <button type="button" className="report-nav-btn" onClick={prev} disabled={currentSlide === 0}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polyline points="15 18 9 12 15 6" /></svg>
          </button>
          <button type="button" className="report-nav-btn" onClick={next} disabled={currentSlide === totalSlides - 1}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polyline points="9 18 15 12 9 6" /></svg>
          </button>
          <span className="rpt-nav-counter">
            {currentSlide + 1} <span style={{ opacity: 0.5 }}>/</span> {totalSlides}
          </span>
        </div>

        <div className="rpt-nav-group">
          <button
            type="button"
            onClick={handleExport}
            className="report-nav-btn rpt-nav-btn-wide"
            title="Download as HTML"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            HTML
          </button>
          <button type="button" className="report-nav-btn" onClick={onClose} title="Close (Esc)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── Reusable sub-components ─────────────────────────────────── */

interface SlideMeta { company: string; date: string }
interface BarRow { label: string; value: number; display: string; color?: string }
interface SpecRow { k: string; v: string }
interface StatItem { value: string; label: string; color?: string }

/** Format a string value to max 2 decimal places if it contains a decimal number */
function fmt2dp(val: string): string {
  return val.replace(/\d+\.\d{3,}/g, match => parseFloat(match).toFixed(2));
}

/** Editorial slide shell: numbered eyebrow, huge serif headline, hairline
    header/footer rules, ghost numeral. Body vertically centers via auto
    margins so dense content still scrolls instead of clipping. */
function Slide({ index, active, section, headline, meta, dense, children }: {
  index: number; active: boolean; section: string; headline: string;
  meta: SlideMeta; dense?: boolean; children: ReactNode;
}) {
  return (
    <div className={`report-slide${active ? ' active' : ''}`} data-slide-index={index}>
      <div className="rpt-ghost" aria-hidden="true">{pad(index)}</div>
      <header className="rpt-head" data-reveal style={rv(0)}>
        <span className="rpt-eyebrow"><span className="rpt-eyebrow-num">{pad(index)}</span>{section}</span>
        <span className="rpt-head-meta">{meta.company}</span>
      </header>
      <h2 className="rpt-headline" data-reveal style={rv(1)} contentEditable suppressContentEditableWarning>{headline}</h2>
      <div className={`rpt-body${dense ? ' rpt-body-dense' : ''}`}>
        <div className="rpt-body-in">{children}</div>
      </div>
      <footer className="rpt-foot" data-reveal style={rv(1)}>
        <span>Dataiku · Quarterly Health Check</span>
        <span>{meta.date}</span>
      </footer>
    </div>
  );
}

/** Row of big serif numbers separated by hairlines — no boxes. */
function Stats({ items, base = 2, small }: { items: StatItem[]; base?: number; small?: boolean }) {
  return (
    <div className={`rpt-stats${small ? ' rpt-stats-sm' : ''}`}>
      {items.map((s, i) => (
        <div key={i} className="rpt-stat" data-reveal style={rv(base + i)}>
          <div className="rpt-stat-value" style={s.color ? { color: s.color } : undefined} contentEditable suppressContentEditableWarning>{fmt2dp(s.value)}</div>
          <div className="rpt-stat-label" contentEditable suppressContentEditableWarning>{s.label}</div>
        </div>
      ))}
    </div>
  );
}

/** LLM narrative as serif paragraphs (bullet chars split into paragraphs). */
function Narrative({ text, compact, rvi = 3 }: { text?: string; compact?: boolean; rvi?: number }) {
  const content = text || 'No analysis available for this section.';
  const lines = content
    .split(/(?=•)|\n+/)
    .map(l => l.replace(/^\s*•\s*/, '').trim())
    .filter(Boolean);
  return (
    <div className={`rpt-narrative${compact ? ' rpt-narrative-compact' : ''}`} data-reveal style={rv(rvi)} contentEditable suppressContentEditableWarning>
      {lines.map((line, i) => <p key={i}>{line}</p>)}
    </div>
  );
}

/** Horizontal bar chart: mono label · hairline track · mono value. */
function Bars({ rows, base = 3, thick }: { rows: BarRow[]; base?: number; thick?: boolean }) {
  const max = Math.max(...rows.map(r => r.value), 1e-9);
  return (
    <div className={`rpt-bars${thick ? ' rpt-bars-thick' : ''}`}>
      {rows.map((r, i) => (
        <div key={i} className="rpt-bar-row" data-reveal style={rv(base + i)}>
          <span className="rpt-bar-label" contentEditable suppressContentEditableWarning>{r.label}</span>
          <span className="rpt-bar-track">
            <span className="rpt-bar-fill" style={{ width: `${Math.max(1.5, (r.value / max) * 100)}%`, background: r.color || MINT, ...rv(i) }} />
          </span>
          <span className="rpt-bar-value" contentEditable suppressContentEditableWarning>{r.display}</span>
        </div>
      ))}
    </div>
  );
}

/** Health score ring with the score set in the serif display face. */
function Gauge({ score, status }: { score: number; status: string }) {
  const R = 84, C = 2 * Math.PI * R;
  const color = status === 'healthy' ? MINT : status === 'warning' ? AMBER : RED;
  const clamped = Math.max(0, Math.min(100, score));
  return (
    <div className="rpt-gauge">
      <svg viewBox="0 0 200 200">
        <circle cx="100" cy="100" r={R} className="rpt-gauge-track" />
        <circle
          cx="100" cy="100" r={R} className="rpt-gauge-arc"
          style={{ stroke: color, strokeDasharray: `${(clamped / 100) * C} ${C}` }}
          transform="rotate(-90 100 100)"
        />
      </svg>
      <div className="rpt-gauge-center">
        <div className="rpt-gauge-score" style={{ color }}>{Math.round(score)}</div>
        <div className="rpt-gauge-status">{status}</div>
      </div>
    </div>
  );
}

/** Em-dash editorial list (highlights, drivers, tuning recs, upgrade paths). */
function EList({ items, mono, arrow, base = 3 }: { items: string[]; mono?: boolean; arrow?: boolean; base?: number }) {
  return (
    <div className={`rpt-elist${mono ? ' rpt-elist-mono' : ''}${arrow ? ' rpt-elist-arrow' : ''}`}>
      {items.map((item, i) => (
        <div key={i} className="rpt-elist-item" data-reveal style={rv(base + i)} contentEditable suppressContentEditableWarning>{item}</div>
      ))}
    </div>
  );
}

/** Outlined warning chips (filesystem warnings, footprint risks). */
function Tags({ items, color, base = 3 }: { items: string[]; color: string; base?: number }) {
  return (
    <div className="rpt-tags">
      {items.map((t, i) => (
        <span key={i} className="rpt-tag" style={{ borderColor: color, color, ...rv(base + i) }} data-reveal contentEditable suppressContentEditableWarning>{t}</span>
      ))}
    </div>
  );
}

/** Spec-sheet definition list (instance overview). */
function Spec({ rows, base = 3 }: { rows: SpecRow[]; base?: number }) {
  return (
    <div className="rpt-spec">
      {rows.map((r, i) => (
        <div key={i} className="rpt-spec-row" data-reveal style={rv(base + i)}>
          <span className="rpt-spec-k" contentEditable suppressContentEditableWarning>{r.k}</span>
          <span className="rpt-spec-v" contentEditable suppressContentEditableWarning>{r.v}</span>
        </div>
      ))}
    </div>
  );
}

/** Recommendation slide: hairline-separated editorial list, outlined numerals. */
function RecSlide({ index, active, section, title, tone, toneLabel, items, meta }: {
  index: number; active: boolean; section: string; title: string;
  tone: 'critical' | 'important' | 'nice'; toneLabel: string;
  items: Array<{ title: string; description: string; impact: string }>;
  meta: SlideMeta;
}) {
  return (
    <Slide index={index} active={active} section={section} headline={title} meta={meta}>
      <div className="rpt-rec-head" data-reveal style={rv(2)}>
        <span className={`rpt-rec-tone rpt-rec-tone-${tone}`}>{toneLabel}</span>
        <span className="rpt-rec-count">{items.length} item{items.length === 1 ? '' : 's'}</span>
      </div>
      <div className={`rpt-rec-list${items.length > 3 ? ' rpt-rec-cols' : ''}`}>
        {items.map((item, i) => (
          <div key={i} className="rpt-rec" data-reveal style={rv(3 + i)}>
            <div className={`rpt-rec-num rpt-rec-num-${tone}`}>{pad(i + 1)}</div>
            <div className="rpt-rec-body">
              <h4 contentEditable suppressContentEditableWarning>{item.title}</h4>
              <p contentEditable suppressContentEditableWarning>{item.description}</p>
              {item.impact && <div className="rpt-rec-impact" contentEditable suppressContentEditableWarning>{item.impact}</div>}
            </div>
          </div>
        ))}
        {items.length === 0 && <div className="rpt-empty">No recommendations in this category.</div>}
      </div>
    </Slide>
  );
}
