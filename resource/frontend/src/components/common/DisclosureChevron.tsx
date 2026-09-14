import { motion, useReducedMotion } from 'framer-motion';

/** Sidebar disclosure motion, shared with expandable rows. */
export function DisclosureChevron({
  expanded,
  className = '',
}: {
  expanded: boolean;
  className?: string;
}) {
  const reduced = useReducedMotion();
  return (
    <motion.svg
      aria-hidden="true"
      initial={false}
      animate={{ rotate: expanded ? 0 : -90 }}
      transition={{ duration: reduced ? 0 : 0.2 }}
      className={`w-3 h-3 shrink-0 ${className}`}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
    </motion.svg>
  );
}
