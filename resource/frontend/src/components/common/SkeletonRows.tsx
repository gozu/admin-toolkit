/**
 * SkeletonRows — placeholder <tr> rows shown in a streaming table's tbody while
 * its scan is active and no rows have arrived yet. Purely presentational; the
 * shimmer (.skeleton-bar) is CSS-driven and degrades to a solid bar under
 * prefers-reduced-motion.
 */
const BAR_WIDTHS = ['60%', '40%', '75%'];

export function SkeletonRows({ rows = 3, cols }: { rows?: number; cols: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r} aria-hidden="true">
          {Array.from({ length: cols }, (_, c) => (
            <td key={c}>
              <div className="skeleton-bar" style={{ width: BAR_WIDTHS[c % BAR_WIDTHS.length] }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
