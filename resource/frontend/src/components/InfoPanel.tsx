import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { motion } from 'framer-motion';
import { useDiag } from '../context/DiagContext';
import { RefreshControl } from './common/RefreshControl';
import { getHostSummary, refreshHostSummary, subscribeHostSummary } from '../state/hostSummary';
import { formatCpuCoresCompact, formatCpuCoresLong } from '../utils/formatters';

function useCountUp(target: number, duration = 1000) {
  const [value, setValue] = useState(0);
  const startTimeRef = useRef<number | null>(null);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    if (target === 0) return;

    startTimeRef.current = null;

    const animate = (timestamp: number) => {
      if (startTimeRef.current === null) startTimeRef.current = timestamp;
      const elapsed = timestamp - startTimeRef.current;
      const progress = Math.min(elapsed / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(eased * target));

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate);
      }
    };

    rafRef.current = requestAnimationFrame(animate);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration]);

  return target === 0 ? 0 : value;
}

function PythonVersionBadge({ version }: { version: string }) {
  const versionMatch = version.match(/(\d+)\.(\d+)/);
  let colorClass = 'text-[var(--text-primary)]';
  let bgClass = 'bg-[var(--bg-glass)] border-[var(--border-glass)]';

  if (versionMatch) {
    const major = parseInt(versionMatch[1], 10);
    const minor = parseInt(versionMatch[2], 10);

    if (major < 3) {
      // Python 2.x - red (EOL)
      colorClass = 'text-[var(--neon-red)]';
      bgClass = 'bg-[var(--status-critical-bg)] border-[var(--status-critical-border)]';
    } else if (major === 3 && minor >= 9) {
      // Python 3.9+ - green (current)
      colorClass = 'text-[var(--neon-green)]';
      bgClass = 'bg-[var(--status-success-bg)] border-[var(--status-success-border)]';
    } else {
      // Python 3.6-3.8 - amber (outdated)
      colorClass = 'text-[var(--neon-amber)]';
      bgClass = 'bg-[var(--status-warning-bg)] border-[var(--status-warning-border)]';
    }
  }

  return (
    <span className={`text-base font-mono font-semibold ${colorClass} ${bgClass} px-2 py-0.5 rounded border`}>
      {version}
    </span>
  );
}

interface MetricCardProps {
  label: string;
  value: React.ReactNode;
  icon: React.ReactNode;
  highlight?: boolean;
  className?: string;
  delay?: number;
  animateValue?: boolean;
}

function AnimatedNumber({ value }: { value: string }) {
  const match = value.match(/^([\d.]+)\s*(.*)$/);
  const num = match ? parseFloat(match[1]) : 0;
  const suffix = match ? match[2] : '';
  const animated = useCountUp(num, 1200);
  if (!match) return <>{value}</>;
  return <>{animated}{suffix ? ` ${suffix}` : ''}</>;
}

function MetricCard({ label, value, icon, highlight = false, className = '', delay = 0, animateValue = false }: MetricCardProps) {
  const displayValue = animateValue && typeof value === 'string' ? <AnimatedNumber value={value} /> : value;

  return (
    <motion.div
      className={`metric-card flex items-center gap-3 ${className}`}
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, delay: delay * 0.1, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className={`metric-card-icon flex-shrink-0 ${highlight ? 'bg-[var(--status-info-bg)] text-[var(--neon-cyan)]' : ''}`}>
        {icon}
      </div>
      <div className="flex flex-col min-w-0">
        <span className="metric-card-label">{label}</span>
        <span className={`metric-card-value truncate ${highlight ? 'text-neon-subtle' : ''}`}>
          {displayValue}
        </span>
      </div>
    </motion.div>
  );
}

// Icon components
const ServerIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
  </svg>
);

const CodeIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
  </svg>
);

const ClockIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
  </svg>
);

const CpuIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
  </svg>
);

const MemoryIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
  </svg>
);

const DesktopIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
  </svg>
);

const BuildingIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
  </svg>
);

const FingerPrintIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 11.5V14m0-2.5v-6a1.5 1.5 0 113 0m-3 6a1.5 1.5 0 00-3 0v2a7.5 7.5 0 0015 0v-5a1.5 1.5 0 00-3 0m-6-3V11m0-5.5v-1a1.5 1.5 0 013 0v1m0 0V11m0-5.5a1.5 1.5 0 013 0v3m0 0V11" />
  </svg>
);

const HashIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14" />
  </svg>
);

function CopyableValue({ value, label }: { value: string; label: string }) {
  const handleCopy = () => {
    navigator.clipboard.writeText(value);
  };

  return (
    <button
      type="button"
      className="appearance-none bg-transparent border-0 p-0 text-left cursor-pointer hover:text-[var(--neon-cyan)] transition-colors group flex items-center gap-1"
      onClick={handleCopy}
      title={`Click to copy ${label}`}
    >
      <span className="truncate">{value}</span>
      <svg
        className="w-3.5 h-3.5 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
      </svg>
    </button>
  );
}

export function InfoPanel() {
  const { state, setParsedData } = useDiag();
  const { parsedData } = state;
  const hostSummary = useSyncExternalStore(subscribeHostSummary, getHostSummary, getHostSummary);
  const canRefresh = state.dataSource === 'api';

  return (
    <motion.div
      id="overview"
      className="glass-card p-5 h-full flex flex-col"
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      {canRefresh && (
        <div className="flex items-center justify-end mb-3">
          <RefreshControl
            busy={hostSummary.status === 'loading'}
            fetchedAt={hostSummary.status === 'done' ? hostSummary.fetchedAt : null}
            onRefresh={() => void refreshHostSummary(setParsedData)}
            title="Re-run host info (cpuinfo / free -m / df / ulimit)"
          />
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 flex-1 auto-rows-fr">
        {/* Company */}
        {parsedData.company && (
          <MetricCard
            label="Company"
            value={parsedData.company}
            icon={<BuildingIcon />}
            className="sm:col-span-2 lg:col-span-1"
            delay={0}
          />
        )}

        {/* DSS Version */}
        {parsedData.dssVersion && (
          <MetricCard
            label="DSS Version"
            value={parsedData.dssVersion}
            icon={<ServerIcon />}
            highlight
            delay={1}
          />
        )}

        {/* Python Version */}
        {parsedData.pythonVersion && (
          <motion.div
            className="metric-card flex items-center gap-3"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-50px' }}
            transition={{ duration: 0.5, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="metric-card-icon flex-shrink-0">
              <CodeIcon />
            </div>
            <div className="flex flex-col min-w-0">
              <span className="metric-card-label">Python</span>
              <PythonVersionBadge version={parsedData.pythonVersion} />
            </div>
          </motion.div>
        )}

        {/* Last Restart */}
        {parsedData.lastRestartTime && (
          <MetricCard
            label="Last Restart"
            value={parsedData.lastRestartTime}
            icon={<ClockIcon />}
            delay={3}
          />
        )}

        {/* CPU Cores — compact "Nc/Mt", full "N Physical cores / M Logical Cores" on hover */}
        {parsedData.cpuCores && (
          <MetricCard
            label="CPU Cores"
            value={
              <span title={formatCpuCoresLong(parsedData.cpuCores)}>
                {formatCpuCoresCompact(parsedData.cpuCores)}
              </span>
            }
            icon={<CpuIcon />}
            delay={4}
          />
        )}

        {/* Total Memory */}
        {parsedData.memoryInfo?.total && (
          <MetricCard
            label="Memory"
            value={parsedData.memoryInfo.total}
            icon={<MemoryIcon />}
            delay={5}
            animateValue
          />
        )}

        {/* OS Info */}
        {parsedData.osInfo && (
          <MetricCard
            label="OS"
            value={
              <span title={parsedData.osInfo}>
                {parsedData.osInfo.length > 28
                  ? `${parsedData.osInfo.substring(0, 28)}...`
                  : parsedData.osInfo}
              </span>
            }
            icon={<DesktopIcon />}
            className="sm:col-span-2 lg:col-span-1"
            delay={6}
          />
        )}

        {/* Node ID */}
        {parsedData.instanceInfo?.nodeId && (
          <MetricCard
            label="Node ID"
            value={<CopyableValue value={parsedData.instanceInfo.nodeId} label="Node ID" />}
            icon={<FingerPrintIcon />}
            delay={7}
          />
        )}

        {/* Install ID */}
        {parsedData.instanceInfo?.installId && (
          <MetricCard
            label="Install ID"
            value={<CopyableValue value={parsedData.instanceInfo.installId} label="Install ID" />}
            icon={<HashIcon />}
            delay={8}
          />
        )}
      </div>
    </motion.div>
  );
}
