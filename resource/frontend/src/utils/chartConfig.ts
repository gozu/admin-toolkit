/** Shared Chart.js tooltip styling used across chart components */
export const BASE_TOOLTIP_STYLE = {
  backgroundColor: 'rgba(22, 28, 36, 0.95)',
  titleFont: { size: 13, family: "'Roboto', sans-serif" },
  bodyFont: { size: 12, family: "'JetBrains Mono', monospace" },
  padding: 12,
  cornerRadius: 8,
  borderColor: 'rgba(107, 167, 210, 0.5)',
  borderWidth: 1,
  titleColor: '#f0f0f5',
  bodyColor: '#a0a0b0',
} as const;

/** Build legend label config with optional overrides */
export function baseLegendLabels(overrides?: Record<string, unknown>) {
  return {
    padding: 16,
    usePointStyle: true,
    pointStyle: 'circle' as const,
    font: { size: 12, family: "'JetBrains Mono', monospace" },
    color: '#a0a0b0',
    ...overrides,
  };
}
