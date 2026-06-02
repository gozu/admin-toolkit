import { useEffect, useCallback, useState, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { ParsedData } from '../types';
import type { ReportData } from '../utils/prepareReportData';
import { useHealthScore } from '../hooks/useHealthScore';
import { useTheme } from '../hooks/useTheme';
import { exportReportAsHtml } from '../utils/exportReport';
import dkulogo from '../assets/dkulogo.png';

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
  const totalSlides = 18;

  const next = useCallback(() => setCurrentSlide(i => Math.min(i + 1, totalSlides - 1)), []);
  const prev = useCallback(() => setCurrentSlide(i => Math.max(i - 1, 0)), []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
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

  // Color helpers
  const pctColor = (s: string): MetricItem['color'] => {
    const n = parseInt(s);
    if (isNaN(n)) return undefined;
    return n >= 90 ? 'danger' : n >= 70 ? 'warning' : 'success';
  };
  const scoreColor = (n: number | undefined): MetricItem['color'] => {
    if (n == null) return undefined;
    return n < 50 ? 'danger' : n < 80 ? 'warning' : 'success';
  };

  const company = parsedData.company || 'Unknown Instance';
  const date = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  const progressPct = ((currentSlide + 1) / totalSlides) * 100;

  return createPortal(
    <div className="report-overlay" data-theme={theme} ref={overlayRef}>
      <div className="report-slides-container">

        {/* ── Slide 1: Title ──────────────────────────────────────── */}
        <div className={`report-slide report-slide-hero${currentSlide === 0 ? ' active' : ''}`} data-slide-index={0}>
          <div className="report-title-center">
            <img src={dkulogo} alt="Dataiku" id="dku-logo" className="report-title-logo" />
            <div className="report-title-company">{company}</div>
            <div className="report-title-divider" />
            <div className="report-title-sub">Quarterly Health Check</div>
            <div className="report-title-meta">
              {parsedData.dssVersion && `DSS ${parsedData.dssVersion}`}{parsedData.dssVersion ? ' · ' : ''}{date}
            </div>
          </div>
        </div>

        {/* ── Slide 2: Executive Summary ──────────────────────────── */}
        <div className={`report-slide${currentSlide === 1 ? ' active' : ''}`} data-slide-index={1}>
          <div className="report-slide-header">
            <div className="report-slide-number">01</div>
            <div className="report-slide-title">Executive Summary</div>
          </div>
          <div className="report-exec-layout">
            <div className="report-exec-score">
              <div className={`report-health-score ${healthScore.status}`}>
                {healthScore.overall}
              </div>
              <div className="report-metric-label" style={{ textAlign: 'center' }}>Health Score</div>
              <div className={`report-badge report-badge-${healthScore.status === 'healthy' ? 'nice' : healthScore.status === 'warning' ? 'important' : 'critical'}`}>
                {healthScore.status}
              </div>
            </div>
            <div className="report-exec-content">
              <NarrativeText text={slides?.executive_summary?.overall_status} />
              <div className="report-findings-grid">
                {(slides?.executive_summary?.findings || []).slice(0, 3).map((f, i) => (
                  <div key={i} className="report-finding-card">
                    <div className="report-finding-number">{i + 1}</div>
                    <div contentEditable suppressContentEditableWarning>{f}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ── Slide 3: Instance Overview ──────────────────────────── */}
        <DataSlide index={2} slideNum="02" active={currentSlide === 2} title="Instance Overview"
          metrics={[
            { value: parsedData.dssVersion || '—', label: 'DSS Version' },
            { value: parsedData.cpuCores || '—', label: 'CPU Cores' },
            { value: parsedData.pythonVersion || '—', label: 'Python' },
            { value: parsedData.osInfo?.split(' ')[0] || '—', label: 'OS' },
          ]}
          narrative={slides?.instance_overview?.narrative}
        />

        {/* ── Slide 4: Projects Overview ──────────────────────────── */}
        <DataSlide index={3} slideNum="03" active={currentSlide === 3} title="Projects Overview"
          metrics={[
            { value: String(parsedData.projects?.length ?? '—'), label: 'Total Projects' },
            { value: String(healthScore.categories.find(c => c.category === 'project_footprint')?.score ?? '—'), label: 'Project Health', color: scoreColor(healthScore.categories.find(c => c.category === 'project_footprint')?.score) },
          ]}
          narrative={slides?.projects?.narrative}
          extras={slides?.projects?.highlights?.length ? (
            <div className="report-extras-list">
              {slides.projects.highlights.map((h, i) => (
                <div key={i} className="report-extras-item">• {h}</div>
              ))}
            </div>
          ) : undefined}
        />

        {/* ── Slide 5: Project Footprint ──────────────────────────── */}
        <DataSlide index={4} slideNum="04" active={currentSlide === 4} title="Project Footprint"
          metrics={[
            { value: String(parsedData.projectFootprintSummary?.projectCount ?? parsedData.projectFootprint?.length ?? '—'), label: 'Projects Analyzed' },
            { value: parsedData.projectFootprintSummary?.instanceAvgProjectGB != null ? `${parsedData.projectFootprintSummary.instanceAvgProjectGB.toFixed(1)} GB` : '—', label: 'Avg Project Size' },
          ]}
          narrative={slides?.project_footprint?.narrative}
          extras={slides?.project_footprint?.risks?.length ? (
            <div className="report-extras-badges">
              {slides.project_footprint.risks.map((r, i) => (
                <span key={i} className="report-badge report-badge-important">{r}</span>
              ))}
            </div>
          ) : undefined}
        />

        {/* ── Slide 6: Code Environments ──────────────────────────── */}
        <DataSlide index={5} slideNum="05" active={currentSlide === 5} title="Code Environments"
          metrics={[
            { value: String(parsedData.codeEnvs?.length ?? '—'), label: 'Total Envs' },
            { value: String(Object.keys(parsedData.pythonVersionCounts || {}).length), label: 'Python Versions' },
            { value: String(Object.keys(parsedData.rVersionCounts || {}).length || '0'), label: 'R Versions' },
          ]}
          narrative={slides?.code_envs?.narrative}
        />

        {/* ── Slide 7: Code Env Health ────────────────────────────── */}
        <DataSlide index={6} slideNum="06" active={currentSlide === 6} title="Code Environment Health"
          metrics={[
            { value: String(healthScore.categories.find(c => c.category === 'code_envs')?.score ?? '—'), label: 'Env Health Score', color: scoreColor(healthScore.categories.find(c => c.category === 'code_envs')?.score) },
            { value: String(parsedData.codeEnvs?.filter(e => e.usageCount === 0).length ?? '0'), label: 'Unused Envs' },
          ]}
          narrative={slides?.code_env_health?.narrative}
          extras={slides?.code_env_health?.upgrade_paths?.length ? (
            <div className="report-extras-list">
              {slides.code_env_health.upgrade_paths.map((u, i) => (
                <div key={i} className="report-extras-item">→ {u}</div>
              ))}
            </div>
          ) : undefined}
        />

        {/* ── Slide 8: Filesystem ─────────────────────────────────── */}
        <DataSlide index={7} slideNum="07" active={currentSlide === 7} title="Filesystem Health"
          metrics={
            (parsedData.filesystemInfo || []).slice(0, 4).map(f => ({
              value: f['Use%'] || '—',
              label: f['Mounted on'] || f.Filesystem,
              color: pctColor(f['Use%'] || ''),
            }))
          }
          narrative={slides?.filesystem?.narrative}
          extras={slides?.filesystem?.warnings?.length ? (
            <div className="report-extras-badges">
              {slides.filesystem.warnings.map((w, i) => (
                <span key={i} className="report-badge report-badge-critical">{w}</span>
              ))}
            </div>
          ) : undefined}
        />

        {/* ── Slide 9: Memory & JVM ───────────────────────────────── */}
        <DataSlide index={8} slideNum="08" active={currentSlide === 8} title="Memory & JVM"
          metrics={[
            { value: parsedData.javaMemorySettings?.Xmx || parsedData.javaMemoryLimits?.Xmx || '—', label: 'Max Heap (Xmx)' },
            { value: parsedData.javaMemorySettings?.Xms || parsedData.javaMemoryLimits?.Xms || '—', label: 'Init Heap (Xms)' },
            { value: parsedData.memoryInfo?.total || parsedData.memoryInfo?.['Mem:total'] || '—', label: 'System RAM' },
            { value: parsedData.memoryInfo?.available || parsedData.memoryInfo?.['Mem:available'] || '—', label: 'Available' },
          ]}
          narrative={slides?.memory?.narrative}
          extras={slides?.memory?.tuning_recs?.length ? (
            <div className="report-extras-list">
              {slides.memory.tuning_recs.map((r, i) => (
                <div key={i} className="report-extras-item">• {r}</div>
              ))}
            </div>
          ) : undefined}
        />

        {/* ── Slide 10: Connections ────────────────────────────────── */}
        <DataSlide index={9} slideNum="09" active={currentSlide === 9} title="Connections"
          metrics={(() => {
            const counts = parsedData.connectionCounts || {};
            const topTypes = Object.entries(counts).sort(([,a],[,b]) => b - a).slice(0, 2);
            return [
              { value: String(parsedData.connectionDetails?.length ?? (Object.values(counts).reduce((a, b) => a + b, 0) || '—')), label: 'Total Connections' },
              { value: String(Object.keys(counts).length), label: 'Connection Types' },
              ...(topTypes[0] ? [{ value: String(topTypes[0][1]), label: topTypes[0][0] }] : []),
              ...(topTypes[1] ? [{ value: String(topTypes[1][1]), label: topTypes[1][0] }] : []),
            ];
          })()}
          narrative={slides?.connections?.narrative}
        />

        {/* ── Slide 11: Issues & Risks ────────────────────────────── */}
        <DataSlide index={10} slideNum="10" active={currentSlide === 10} title="Issues & Risks"
          metrics={[
            { value: String(Object.keys(parsedData.disabledFeatures || {}).length), label: 'Disabled Features' },
            { value: slides?.issues?.risk_level?.toUpperCase() || '—', label: 'Risk Level' },
            { value: String(parsedData.pluginDetails?.length ?? parsedData.plugins?.length ?? '—'), label: 'Plugins' },
            { value: String(parsedData.clusters?.length ?? '0'), label: 'Clusters' },
          ]}
          narrative={slides?.issues?.narrative}
        />

        {/* ── Slide 12: Users & Activity ──────────────────────────── */}
        <DataSlide index={11} slideNum="11" active={currentSlide === 11} title="Users & Activity"
          metrics={[
            { value: String(parsedData.users?.length ?? '—'), label: 'Total Users' },
            { value: String(parsedData.users?.filter(u => u.enabled !== false).length ?? '—'), label: 'Active Users' },
            { value: String(parsedData.users?.filter(u => u.userProfile === 'DATA_SCIENTIST' || u.userProfile === 'DESIGNER').length ?? '—'), label: 'Designers' },
            { value: String(parsedData.projects?.length ?? '—'), label: 'Projects' },
          ]}
          narrative={slides?.users?.narrative}
        />

        {/* ── Slide 13: Log Analysis ──────────────────────────────── */}
        <DataSlide index={12} slideNum="12" active={currentSlide === 12} title="Log Analysis"
          metrics={[
            { value: String(parsedData.logStats?.['Unique Errors'] ?? '—'), label: 'Unique Errors' },
            { value: String(parsedData.logStats?.['Total Lines'] ?? '—'), label: 'Total Log Lines' },
            { value: String(parsedData.logStats?.['Displayed Errors'] ?? '—'), label: 'Displayed' },
            { value: parsedData.rawLogErrors?.length ? String(parsedData.rawLogErrors.length) : '—', label: 'Error Blocks' },
          ]}
          narrative={slides?.logs?.narrative}
          extras={slides?.logs?.patterns?.length ? (
            <div className="report-extras-list">
              {slides.logs.patterns.slice(0, 5).map((p, i) => (
                <div key={i} className="report-extras-item" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: '#816948' }}>{p}</div>
              ))}
            </div>
          ) : undefined}
        />

        {/* ── Slide 14: Recommendations - Critical ────────────────── */}
        <RecSlide index={13} slideNum="13" active={currentSlide === 13}
          title="Critical Recommendations"
          badgeClass="report-badge-critical" badgeText="CRITICAL" numberClass="report-rec-number-critical"
          items={slides?.rec_critical?.items || []}
        />

        {/* ── Slide 15: Recommendations - Important ───────────────── */}
        <RecSlide index={14} slideNum="14" active={currentSlide === 14}
          title="Important Recommendations"
          badgeClass="report-badge-important" badgeText="IMPORTANT" numberClass="report-rec-number-important"
          items={slides?.rec_important?.items || []}
        />

        {/* ── Slide 16: Recommendations - Nice to Have ────────────── */}
        <RecSlide index={15} slideNum="15" active={currentSlide === 15}
          title="Optimization Opportunities"
          badgeClass="report-badge-nice" badgeText="NICE TO HAVE" numberClass="report-rec-number-nice"
          items={slides?.rec_nice_to_have?.items || []}
        />

        {/* ── Slide 17: Action Plan ───────────────────────────────── */}
        <div className={`report-slide${currentSlide === 16 ? ' active' : ''}`} data-slide-index={16}>
          <div className="report-slide-header">
            <div className="report-slide-number">16</div>
            <div>
              <div className="report-slide-title">Action Plan</div>
              <div className="report-slide-subtitle">Prioritized roadmap for the next quarter</div>
            </div>
          </div>
          <div className="report-action-list">
            {(slides?.action_plan?.priorities || []).map((p, i) => (
              <div key={i} className="report-action-row">
                <div className="report-action-step">{i + 1}</div>
                <span className="action-text" contentEditable suppressContentEditableWarning>{p.action}</span>
                <span className="action-timeline">{p.timeline}</span>
                <span className={`report-badge report-badge-effort-${p.effort || 'medium'}`}>{p.effort || 'medium'}</span>
              </div>
            ))}
            {(!slides?.action_plan?.priorities?.length) && (
              <div style={{ color: '#816948', fontSize: '0.9rem', paddingLeft: '1rem' }}>No action items generated.</div>
            )}
          </div>
          <SlideWatermark />
        </div>

        {/* ── Slide 18: Closing ───────────────────────────────────── */}
        <div className={`report-slide report-slide-hero${currentSlide === 17 ? ' active' : ''}`} data-slide-index={17}>
          <div className="report-title-center">
            <img src={dkulogo} alt="Dataiku" id="dku-logo-closing" className="report-title-logo" style={{ width: 56, height: 56 }} />
            <div style={{ fontSize: '2.25rem', fontWeight: 800, color: '#FEFEF9', letterSpacing: '-0.03em', fontFamily: 'var(--font-display)' }}>Next Steps</div>
            <div className="report-title-divider" />
            <div style={{ fontSize: '1rem', color: 'rgba(254,254,249,0.7)', maxWidth: 500, lineHeight: 1.7, textAlign: 'center' }}>
              Review the recommendations with your team and prioritize based on your operational needs.
              Your Technical Account Manager is available for follow-up discussions.
            </div>
            <div className="report-title-meta">{company} · {date}</div>
          </div>
        </div>
      </div>

      {/* ── Navigation ─────────────────────────────────────────── */}
      <div className="report-nav">
        <div className="report-progress-bar" style={{ width: `${progressPct}%` }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button type="button" className="report-nav-btn" onClick={prev} disabled={currentSlide === 0}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polyline points="15 18 9 12 15 6" /></svg>
          </button>
          <button type="button" className="report-nav-btn" onClick={next} disabled={currentSlide === totalSlides - 1}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polyline points="9 18 15 12 9 6" /></svg>
          </button>
          <span style={{ fontSize: '0.85rem', color: 'rgba(254,254,249,0.6)', marginLeft: '0.75rem', fontWeight: 500, fontFamily: 'var(--font-mono)' }}>
            {currentSlide + 1} <span style={{ opacity: 0.5 }}>/</span> {totalSlides}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <button
            type="button"
            onClick={handleExport}
            className="report-nav-btn"
            style={{ width: 'auto', padding: '0.35rem 0.75rem', fontSize: '0.75rem', gap: '0.35rem', display: 'flex' }}
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

interface MetricItem { value: string; label: string; color?: 'success' | 'warning' | 'danger' }

/** Format a string value to max 2 decimal places if it contains a decimal number */
function fmt2dp(val: string): string {
  return val.replace(/\d+\.\d{3,}/g, match => parseFloat(match).toFixed(2));
}

/** Render narrative text with bullet points on separate lines, editable */
function NarrativeText({ text }: { text?: string }) {
  const content = text || 'No analysis available for this section.';
  // Split on bullet character or newlines to ensure each bullet is its own line
  const lines = content.split(/(?=\u2022)|(?:\n)+/).filter(l => l.trim());
  return (
    <div className="report-narrative" contentEditable suppressContentEditableWarning>
      {lines.map((line, i) => (
        <div key={i} style={{ marginBottom: i < lines.length - 1 ? '0.5em' : 0 }}>{line.trim()}</div>
      ))}
    </div>
  );
}

function SlideWatermark() {
  return (
    <div className="report-watermark">
      <div style={{ width: 14, height: 14, opacity: 0.5 }}>
        <img src={dkulogo} alt="" style={{ width: '100%', height: '100%' }} />
      </div>
      <span style={{ fontFamily: 'var(--font-mono)', color: '#816948' }}>&copy;2026 Dataiku Inc.</span>
    </div>
  );
}

function DataSlide({ index, slideNum, active, title, metrics, narrative, extras }: {
  index: number; slideNum: string; active: boolean; title: string;
  metrics: MetricItem[]; narrative?: string; extras?: React.ReactNode;
}) {
  return (
    <div className={`report-slide${active ? ' active' : ''}`} data-slide-index={index}>
      <div className="report-slide-header">
        <div className="report-slide-number">{slideNum}</div>
        <div className="report-slide-title">{title}</div>
      </div>
      <div className="report-two-col">
        <div className="report-metrics-grid">
          {metrics.map((m, i) => (
            <div key={i} className="report-metric">
              <div className="report-metric-value" style={m.color ? { color: `var(--${m.color})` } : undefined}>{fmt2dp(m.value)}</div>
              <div className="report-metric-label">{m.label}</div>
            </div>
          ))}
        </div>
        <div>
          <NarrativeText text={narrative} />
          {extras}
        </div>
      </div>
      <SlideWatermark />
    </div>
  );
}

function RecSlide({ index, slideNum, active, title, badgeClass, badgeText, numberClass, items }: {
  index: number; slideNum: string; active: boolean; title: string;
  badgeClass: string; badgeText: string; numberClass: string;
  items: Array<{ title: string; description: string; impact: string }>;
}) {
  return (
    <div className={`report-slide${active ? ' active' : ''}`} data-slide-index={index}>
      <div className="report-slide-header">
        <div className="report-slide-number">{slideNum}</div>
        <div className="report-slide-title">{title}</div>
        <span className={`report-badge ${badgeClass}`}>{badgeText}</span>
      </div>
      <div className="report-rec-list">
        {items.map((item, i) => (
          <div key={i} className="report-rec-card">
            <div className={`report-rec-number ${numberClass}`}>{i + 1}</div>
            <div style={{ flex: 1 }}>
              <h4 contentEditable suppressContentEditableWarning>{item.title}</h4>
              <p contentEditable suppressContentEditableWarning>{item.description}</p>
              {item.impact && (
                <div className="report-rec-impact">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}><polyline points="20 6 9 17 4 12" /></svg>
                  {item.impact}
                </div>
              )}
            </div>
          </div>
        ))}
        {items.length === 0 && (
          <div style={{ color: '#816948', fontSize: '0.9rem' }}>No recommendations in this category.</div>
        )}
      </div>
      <SlideWatermark />
    </div>
  );
}
