import { motion } from 'framer-motion';
import type { ParsedData } from '../../types';

interface ComparisonMemoryAnalysisCardProps {
  beforeData: ParsedData;
  afterData: ParsedData;
}

interface MemoryValues {
  totalVm: number;
  totalVmStr: string;
  recommendedMax: number;
  cgroupLimit: number;
  cgroupLimitStr: string;
  cgroupOverageGB: number;
  jekGB: number;
  maxJobs: number;
  jekTotal: number;
  availableGB: number;
  cgroupStatus: 'ok' | 'warning' | 'critical';
  jekStatus: 'ok' | 'warning' | 'critical';
}

function parseMemoryValues(data: ParsedData): MemoryValues | null {
  const totalVmStr = data.memoryInfo?.total || '';
  const jekStr = data.javaMemorySettings?.JEK || '0g';
  const maxActivitiesRaw = data.maxRunningActivities?.['Max Running Activities'];
  const cgroupLimitStr = String(data.cgroupSettings?.['Memory Limit'] || '0');

  const totalVm = parseInt(totalVmStr.replace(/[^0-9]/g, '')) || 0;
  const jekGB = parseInt(jekStr.replace(/[^0-9]/g, '')) || 0;
  const maxActivities = typeof maxActivitiesRaw === 'number' ? maxActivitiesRaw : 0;
  const cgroupLimit = parseInt(cgroupLimitStr.replace(/[^0-9]/g, '')) || 0;
  const maxRunningJobs = data.jekSettings?.maxRunningJobs ?? 0;

  // JEK is per-job. maxRunningJobs caps concurrent jobs when set; otherwise worst case
  // is one job per activity.
  const maxJobs = maxRunningJobs > 0 ? Math.min(maxRunningJobs, maxActivities) : maxActivities;

  if (cgroupLimit === 0) return null;

  // Calculate recommended max based on total memory
  let recommendedMax = 0;
  if (totalVm > 120) {
    recommendedMax = Math.floor(totalVm * 0.75);
  } else if (totalVm > 60) {
    recommendedMax = Math.floor(totalVm * 0.66);
  } else if (totalVm > 30) {
    recommendedMax = totalVm - 20;
  } else {
    recommendedMax = Math.floor(totalVm * 0.5);
  }

  const cgroupOverageGB = cgroupLimit - recommendedMax;
  const jekTotal = jekGB * maxJobs;
  const availableGB = cgroupLimit - jekTotal;

  const cgroupStatus = cgroupOverageGB > 0 ? 'warning' : 'ok';
  const jekStatus = availableGB < 0 ? 'critical' : availableGB < 16 ? 'warning' : 'ok';

  return {
    totalVm,
    totalVmStr,
    recommendedMax,
    cgroupLimit,
    cgroupLimitStr,
    cgroupOverageGB,
    jekGB,
    maxJobs,
    jekTotal,
    availableGB,
    cgroupStatus,
    jekStatus,
  };
}

function DeltaIndicator({ before, after, lowerIsBetter = false }: { before: number; after: number; lowerIsBetter?: boolean }) {
  const delta = after - before;
  if (delta === 0) return null;

  const isImprovement = lowerIsBetter ? delta < 0 : delta > 0;
  const color = isImprovement ? 'var(--neon-green)' : 'var(--neon-red)';
  const sign = delta > 0 ? '+' : '';

  return (
    <span className="ml-1 text-xs font-mono" style={{ color }}>
      ({sign}{delta})
    </span>
  );
}

function StatusDot({ status }: { status: 'ok' | 'warning' | 'critical' }) {
  const colors = { ok: 'var(--neon-green)', warning: 'var(--neon-yellow)', critical: 'var(--neon-red)' };
  return (
    <span
      className="inline-block w-2 h-2 rounded-full ml-1"
      style={{ backgroundColor: colors[status] }}
    />
  );
}

export function ComparisonMemoryAnalysisCard({ beforeData, afterData }: ComparisonMemoryAnalysisCardProps) {
  const before = parseMemoryValues(beforeData);
  const after = parseMemoryValues(afterData);

  // Need at least one side with valid data
  if (!before && !after) return null;

  const statusColors = { ok: 'var(--neon-green)', warning: 'var(--neon-yellow)', critical: 'var(--neon-red)' };

  // Determine if there's an improvement or regression in available memory
  const availableDelta = (after?.availableGB ?? 0) - (before?.availableGB ?? 0);
  const hasChange = before && after && availableDelta !== 0;
  const overallDirection = availableDelta > 0 ? 'improvement' : availableDelta < 0 ? 'regression' : 'neutral';

  return (
    <motion.div
      className="chart-container"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>Memory Analysis</h4>
        {hasChange && (
          <span className={`text-sm font-mono ${overallDirection === 'improvement' ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}`}>
            {availableDelta > 0 ? '+' : ''}{availableDelta} GB available
          </span>
        )}
      </div>

      <div className="p-4">
        {/* CGroup Limit Check Comparison */}
        <div className="text-xs text-[var(--text-secondary)] mb-2 font-medium">CGroup Limit Check</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[var(--text-muted)] text-xs">
                <th className="text-left font-normal pb-2">Metric</th>
                <th className="text-right font-normal pb-2">Before</th>
                <th className="text-right font-normal pb-2">After</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="text-[var(--text-secondary)] py-1">VM Total</td>
                <td className="text-right font-mono text-[var(--text-muted)]">{before?.totalVmStr || '—'}</td>
                <td className="text-right font-mono text-[var(--text-primary)]">
                  {after?.totalVmStr || '—'}
                  {before && after && <DeltaIndicator before={before.totalVm} after={after.totalVm} />}
                </td>
              </tr>
              <tr>
                <td className="text-[var(--text-secondary)] py-1">Recommended Max</td>
                <td className="text-right font-mono text-[var(--text-muted)]">{before ? `${before.recommendedMax} GB` : '—'}</td>
                <td className="text-right font-mono text-[var(--text-primary)]">
                  {after ? `${after.recommendedMax} GB` : '—'}
                  {before && after && <DeltaIndicator before={before.recommendedMax} after={after.recommendedMax} />}
                </td>
              </tr>
              <tr>
                <td className="text-[var(--text-secondary)] py-1">Configured Limit</td>
                <td className="text-right font-mono" style={{ color: before ? statusColors[before.cgroupStatus] : 'var(--text-muted)' }}>
                  {before?.cgroupLimitStr || '—'}
                  {before && <StatusDot status={before.cgroupStatus} />}
                </td>
                <td className="text-right font-mono" style={{ color: after ? statusColors[after.cgroupStatus] : 'var(--text-primary)' }}>
                  {after?.cgroupLimitStr || '—'}
                  {after && <StatusDot status={after.cgroupStatus} />}
                  {before && after && <DeltaIndicator before={before.cgroupLimit} after={after.cgroupLimit} />}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* CGroup warnings */}
        {(before?.cgroupStatus !== 'ok' || after?.cgroupStatus !== 'ok') && (
          <div className="mt-2 flex gap-2 text-xs">
            {before && before.cgroupStatus !== 'ok' && (
              <div className="flex-1 p-2 rounded opacity-60" style={{ backgroundColor: `color-mix(in srgb, ${statusColors[before.cgroupStatus]} 10%, transparent)`, border: `1px solid color-mix(in srgb, ${statusColors[before.cgroupStatus]} 30%, transparent)`, color: statusColors[before.cgroupStatus] }}>
                Before: +{before.cgroupOverageGB}GB over
              </div>
            )}
            {after && after.cgroupStatus !== 'ok' && (
              <div className="flex-1 p-2 rounded" style={{ backgroundColor: `color-mix(in srgb, ${statusColors[after.cgroupStatus]} 10%, transparent)`, border: `1px solid color-mix(in srgb, ${statusColors[after.cgroupStatus]} 30%, transparent)`, color: statusColors[after.cgroupStatus] }}>
                After: +{after.cgroupOverageGB}GB over
              </div>
            )}
          </div>
        )}

        {/* Divider */}
        <div className="my-3 border-t border-[var(--border-color)] opacity-30" />

        {/* JEK Allocation Check Comparison */}
        <div className="text-xs text-[var(--text-secondary)] mb-2 font-medium">JEK Allocation Check</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[var(--text-muted)] text-xs">
                <th className="text-left font-normal pb-2">Metric</th>
                <th className="text-right font-normal pb-2">Before</th>
                <th className="text-right font-normal pb-2">After</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="text-[var(--text-secondary)] py-1">CGroup Limit</td>
                <td className="text-right font-mono text-[var(--text-muted)]">{before?.cgroupLimitStr || '—'}</td>
                <td className="text-right font-mono text-[var(--text-primary)]">{after?.cgroupLimitStr || '—'}</td>
              </tr>
              <tr>
                <td className="text-[var(--text-secondary)] py-1 pl-2">JEK × Max Jobs</td>
                <td className="text-right font-mono text-[var(--neon-cyan)] opacity-60">
                  {before ? `- ${before.jekTotal} GB` : '—'}
                </td>
                <td className="text-right font-mono text-[var(--neon-cyan)]">
                  {after ? `- ${after.jekTotal} GB` : '—'}
                  {before && after && <DeltaIndicator before={before.jekTotal} after={after.jekTotal} lowerIsBetter />}
                </td>
              </tr>
              <tr className="border-t border-[var(--border-color)]">
                <td className="font-medium pt-2" style={{ color: after ? statusColors[after.jekStatus] : 'var(--text-primary)' }}>
                  Available for Backend
                </td>
                <td className="text-right font-mono pt-2 opacity-60" style={{ color: before ? statusColors[before.jekStatus] : 'var(--text-muted)' }}>
                  {before ? `${before.availableGB} GB` : '—'}
                  {before && <StatusDot status={before.jekStatus} />}
                </td>
                <td className="text-right font-mono font-bold pt-2" style={{ color: after ? statusColors[after.jekStatus] : 'var(--text-primary)' }}>
                  {after ? `${after.availableGB} GB` : '—'}
                  {after && <StatusDot status={after.jekStatus} />}
                  {before && after && <DeltaIndicator before={before.availableGB} after={after.availableGB} />}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* JEK warnings */}
        {(before?.jekStatus !== 'ok' || after?.jekStatus !== 'ok') && (
          <div className="mt-2 flex gap-2 text-xs">
            {before && before.jekStatus !== 'ok' && (
              <div className="flex-1 p-2 rounded opacity-60" style={{ backgroundColor: `color-mix(in srgb, ${statusColors[before.jekStatus]} 10%, transparent)`, border: `1px solid color-mix(in srgb, ${statusColors[before.jekStatus]} 30%, transparent)`, color: statusColors[before.jekStatus] }}>
                {before.jekStatus === 'critical' ? `Before: Over by ${Math.abs(before.availableGB)}GB` : `Before: ${before.availableGB}GB headroom`}
              </div>
            )}
            {after && after.jekStatus !== 'ok' && (
              <div className="flex-1 p-2 rounded" style={{ backgroundColor: `color-mix(in srgb, ${statusColors[after.jekStatus]} 10%, transparent)`, border: `1px solid color-mix(in srgb, ${statusColors[after.jekStatus]} 30%, transparent)`, color: statusColors[after.jekStatus] }}>
                {after.jekStatus === 'critical' ? `After: Over by ${Math.abs(after.availableGB)}GB` : `After: ${after.availableGB}GB headroom`}
              </div>
            )}
          </div>
        )}

        {/* Status improvement/regression summary */}
        {before && after && (before.jekStatus !== after.jekStatus || before.cgroupStatus !== after.cgroupStatus) && (
          <div className="mt-3 pt-3 border-t border-[var(--border-color)] opacity-30">
            <div className="flex items-center justify-center gap-4 text-xs">
              {before.jekStatus !== after.jekStatus && (
                <span className={after.jekStatus === 'ok' ? 'text-[var(--neon-green)]' : after.jekStatus === 'critical' ? 'text-[var(--neon-red)]' : 'text-[var(--neon-yellow)]'}>
                  JEK: {before.jekStatus} → {after.jekStatus}
                </span>
              )}
              {before.cgroupStatus !== after.cgroupStatus && (
                <span className={after.cgroupStatus === 'ok' ? 'text-[var(--neon-green)]' : 'text-[var(--neon-yellow)]'}>
                  CGroup: {before.cgroupStatus} → {after.cgroupStatus}
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </motion.div>
  );
}
