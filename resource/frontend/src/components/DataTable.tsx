import { motion } from 'framer-motion';
import { formatKey } from '../utils/formatters';

type CellValue =
  | string
  | number
  | boolean
  | { value: string; truncate: boolean; maxLength: number }
  | { status: string; description: string; url: string };

interface DataTableProps {
  id: string;
  title: string;
  data: Record<string, CellValue>;
  sortNumeric?: boolean;
  showHeaders?: boolean;
  headers?: string[];
}

export function DataTable({
  id,
  title,
  data,
  sortNumeric = false,
  showHeaders = true,
  headers = ['Name', 'Value'],
}: DataTableProps) {
  if (!data || Object.keys(data).length === 0) {
    return null;
  }

  let entries = Object.entries(data);

  // Handle special sorting cases
  if (id === 'sparkSettings-table' && data['Spark Version']) {
    entries = entries.filter(([key]) => key !== 'Spark Version');
    entries.unshift(['Spark Version', data['Spark Version']]);
  } else if (id === 'cgroupSettings-table') {
    // Custom order for CGroups settings
    const cgroupOrder = [
      'Enabled',
      'Configured Target Types',
      'Empty Target Types',
      'Memory Limit',
    ];
    const orderedEntries: [string, CellValue][] = [];
    const remainingEntries: [string, CellValue][] = [];

    // First, add entries in the specified order
    for (const key of cgroupOrder) {
      const entry = entries.find(([k]) => k === key);
      if (entry) {
        orderedEntries.push(entry);
      }
    }

    // Then add remaining entries
    for (const entry of entries) {
      if (!cgroupOrder.includes(entry[0])) {
        remainingEntries.push(entry);
      }
    }

    entries = [...orderedEntries, ...remainingEntries];
  } else if (sortNumeric) {
    entries.sort((a, b) => {
      const valA = typeof a[1] === 'number' ? a[1] : parseInt(String(a[1])) || 0;
      const valB = typeof b[1] === 'number' ? b[1] : parseInt(String(b[1])) || 0;
      return valB - valA;
    });
  }

  return (
    <motion.div
      className="chart-container"
      id={id}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>{title}</h4>
      </div>
      <div>
        <table className="table-dark">
          {showHeaders && (
            <thead>
              <tr>
                {headers.map((header, idx) => (
                  <th key={idx}>{header}</th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {entries.map(([key, value], idx) => (
              <TableRow key={idx} id={id} rowKey={key} value={value} isOdd={idx % 2 === 0} />
            ))}
          </tbody>
        </table>
      </div>
    </motion.div>
  );
}

interface TableRowProps {
  id: string;
  rowKey: string;
  value: CellValue;
  isOdd: boolean;
}

function TableRow({ id, rowKey, value }: TableRowProps) {
  // Format the key (but NOT for usersByProjects since those are email addresses)
  const formattedKey = id === 'usersByProjects-table' ? rowKey : formatKey(rowKey);

  // Handle disabled features (special 3-column case)
  if (
    id === 'disabledFeatures-table' &&
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    'description' in value &&
    'url' in value
  ) {
    return (
      <tr className="hover:bg-[var(--bg-glass)] transition-colors duration-100">
        <td>
          <a
            href={value.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-[var(--neon-cyan)] hover:text-[var(--neon-cyan)] hover:underline"
          >
            {rowKey}
          </a>
        </td>
        <td>
          <span className="badge badge-critical">
            {value.status}
          </span>
        </td>
        <td className="text-[var(--text-secondary)]">{value.description}</td>
      </tr>
    );
  }

  // Handle truncated values
  if (
    typeof value === 'object' &&
    value !== null &&
    'truncate' in value &&
    value.truncate
  ) {
    const truncatedValue =
      value.value.length > value.maxLength
        ? value.value.substring(0, value.maxLength) + '...'
        : value.value;

    return (
      <tr className="hover:bg-[var(--bg-glass)] transition-colors duration-100">
        <td className="font-medium text-[var(--text-primary)]">{formattedKey}</td>
        <td
          className="text-[var(--text-secondary)] font-mono cursor-help"
          title={value.value}
        >
          {truncatedValue}
        </td>
      </tr>
    );
  }

  // Handle boolean values
  if (typeof value === 'boolean') {
    return (
      <tr className="hover:bg-[var(--bg-glass)] transition-colors duration-100">
        <td className="font-medium text-[var(--text-primary)]">{formattedKey}</td>
        <td>
          <span className={`badge ${value ? 'badge-success' : 'badge-neutral'}`}>
            {value ? 'Yes' : 'No'}
          </span>
        </td>
      </tr>
    );
  }

  // Handle enabled settings
  if (id === 'enabledSettings-table') {
    const enabled = value === 'true' || String(value) === 'true';
    return (
      <tr className="hover:bg-[var(--bg-glass)] transition-colors duration-100">
        <td className="font-medium text-[var(--text-primary)]">{formattedKey}</td>
        <td>
          <span className={`badge ${enabled ? 'badge-success' : 'badge-neutral'}`}>
            {enabled ? 'Yes' : 'No'}
          </span>
        </td>
      </tr>
    );
  }

  // Standard row
  return (
    <tr className="hover:bg-[var(--bg-glass)] transition-colors duration-100">
      <td className="font-medium text-[var(--text-primary)]">{formattedKey}</td>
      <td>
        <ValueCell id={id} rowKey={rowKey} value={value} />
      </td>
    </tr>
  );
}

function ValueCell({
  id,
  rowKey,
  value,
}: {
  id: string;
  rowKey: string;
  value: CellValue;
}) {
  const stringValue = String(value);
  let colorClass = 'text-[var(--text-secondary)]';
  let badgeClass = '';

  // Python version coloring
  if (id === 'pythonVersionCounts-table' || rowKey === 'Python Version') {
    const versionMatch = stringValue.match(/(\d+\.\d+)/);
    if (versionMatch) {
      const versionNum = parseFloat(versionMatch[1]);
      if (versionNum < 3.6) {
        colorClass = 'text-[var(--neon-red)]';
        badgeClass = 'badge badge-critical';
      } else if (versionNum < 3.9) {
        colorClass = 'text-[var(--neon-amber)]';
        badgeClass = 'badge badge-warning';
      }
    }
  }

  // Default Container Execution Config coloring
  if (rowKey === 'Default Execution Config' && stringValue === 'Not set') {
    colorClass = 'text-[var(--neon-amber)]';
    badgeClass = 'badge badge-warning';
  }

  if (badgeClass) {
    return <span className={badgeClass}>{stringValue}</span>;
  }

  return (
    <span className={`${colorClass} font-mono`}>
      {stringValue}
    </span>
  );
}
