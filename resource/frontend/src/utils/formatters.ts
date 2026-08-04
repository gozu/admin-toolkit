/**
 * Format bytes to human-readable string
 */
export function formatBytes(bytes: number, decimals = 2): string {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/**
 * Format camelCase or PascalCase to human-readable string
 */
export function formatCamelCase(
  str: string,
  options: { replaceDots?: boolean; expandAbbreviations?: boolean } = {},
): string {
  let result = str.replace(/([A-Z])/g, ' $1').replace(/^./, (s) => s.toUpperCase());

  if (options.replaceDots) {
    result = result.replace(/\./g, ' ');
  }
  if (options.expandAbbreviations) {
    result = result.replace('Max', 'Maximum').replace('Min', 'Minimum');
  }

  return result.trim();
}

/**
 * Parse numeric value from string (removes non-numeric characters except decimal)
 */
export function parseNumericValue(str: string): number {
  return parseFloat(str.replace(/[^0-9.]/g, ''));
}

/**
 * Format a key for display (replace dots and underscores with spaces)
 */
export function formatKey(key: string): string {
  return key
    .replace(/\./g, ' ')
    .replace(/_/g, ' ')
    .replace(/Settings/g, '')
    .replace(/enabled/g, '')
    .trim();
}

/**
 * Parse size string (like "10G" or "1.5T") to GB
 */
export function parseSizeToGB(sizeStr: string): number {
  if (!sizeStr) return 0;

  const value = parseNumericValue(sizeStr);
  if (sizeStr.includes('T')) {
    return value * 1024; // TB to GB
  } else if (sizeStr.includes('G')) {
    return value; // Already in GB
  } else if (sizeStr.includes('M')) {
    return value / 1024; // MB to GB
  } else if (sizeStr.includes('K')) {
    return value / (1024 * 1024); // KB to GB
  }
  return value;
}

/**
 * Format a kilobyte value (as reported by `ps` RSS/VSZ) to a human-readable
 * string, auto-selecting KB/MB/GB.
 */
export function formatKb(kb: number): string {
  if (kb >= 1024 * 1024) return `${(kb / (1024 * 1024)).toFixed(2)} GB`;
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${kb} KB`;
}

/**
 * Format memory value in MB to human-readable string
 */
export function formatMemory(mbValue: number): string {
  if (mbValue >= 1024) {
    return `${(mbValue / 1024).toFixed(2)} GB`;
  } else {
    return `${mbValue.toLocaleString()} MB`;
  }
}

/**
 * Format date string (YYYYMMDD format) to human-readable
 */
export function formatDateString(dateString: string): string {
  if (dateString && dateString.length === 8) {
    const year = dateString.substring(0, 4);
    const month = dateString.substring(4, 6);
    const day = dateString.substring(6, 8);
    const date = new Date(parseInt(year), parseInt(month) - 1, parseInt(day));
    return (
      date.getDate() +
      ' ' +
      date.toLocaleString('en-US', { month: 'short' }) +
      ' ' +
      date.getFullYear()
    );
  }
  return dateString;
}

/**
 * Format bytes with an auto-selected unit (B/KB/MB/GB/TB) and adaptive precision.
 */
export function formatAuto(bytes: number | undefined): string {
  const value = bytes || 0;
  if (value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  const decimals = size >= 100 ? 0 : size >= 10 ? 1 : 2;
  return `${size.toFixed(decimals)} ${units[idx]}`;
}

/**
 * Return a Tailwind color class based on relative size ratio
 */
export function getRelativeSizeColor(bytes: number, maxBytes: number): string {
  if (maxBytes <= 0) return 'text-[var(--text-muted)]';
  const ratio = bytes / maxBytes;
  if (ratio > 0.5) return 'text-[var(--neon-red)]';
  if (ratio > 0.2) return 'text-[var(--neon-amber)]';
  if (ratio > 0.05) return 'text-[var(--neon-green)]';
  return 'text-[var(--text-muted)]';
}

/**
 * Format a byte value to a fixed "X.XX GB" string
 */
export function formatGb(bytes: number): string {
  return `${((bytes || 0) / 1024 ** 3).toFixed(2)} GB`;
}

/**
 * Format an optional byte count as "X.XX GB", em-dash when missing
 */
export function formatSizeGb(sizeBytes: number | undefined): string {
  if (!sizeBytes) return '—';
  return formatGb(sizeBytes);
}

/**
 * Split a canonical CPU-cores label into physical/logical counts. Handles the
 * backend "N Cores / M Threads" form, the diag-text "N C / M T" form, and a
 * bare "N". `logical` is null when the label carries only one number.
 */
export function parseCpuCoreCounts(cpuCores: string | undefined): {
  physical: number | null;
  logical: number | null;
} {
  const nums = cpuCores?.match(/\d+/g);
  if (!nums || nums.length === 0) return { physical: null, logical: null };
  const physical = parseInt(nums[0], 10);
  const logical = nums.length > 1 ? parseInt(nums[1], 10) : null;
  return {
    physical: Number.isFinite(physical) ? physical : null,
    logical: logical !== null && Number.isFinite(logical) ? logical : null,
  };
}

/**
 * "4 Cores / 8 Threads" → "4 Physical cores / 8 Logical Cores". Falls back to
 * the raw label when it can't be split into two counts.
 */
export function formatCpuCoresLong(cpuCores: string | undefined): string {
  const { physical, logical } = parseCpuCoreCounts(cpuCores);
  if (physical !== null && logical !== null) {
    return `${physical} Physical cores / ${logical} Logical Cores`;
  }
  return cpuCores ?? '';
}

/**
 * "4 Cores / 8 Threads" → "4c/8t". Falls back to the raw label when it can't
 * be split into two counts.
 */
export function formatCpuCoresCompact(cpuCores: string | undefined): string {
  const { physical, logical } = parseCpuCoreCounts(cpuCores);
  if (physical !== null && logical !== null) {
    return `${physical}c/${logical}t`;
  }
  return cpuCores ?? '';
}

/**
 * Format a DSS interpreter token ("PYTHON311") as a dotted version ("3.11").
 * Unrecognised tokens pass through unchanged.
 */
export function formatInterpreter(raw: string): string {
  const match = /^PYTHON(\d)(\d+)$/.exec(raw);
  return match ? `${match[1]}.${match[2]}` : raw;
}
