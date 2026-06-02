import type { ReactNode } from 'react';
import { motion } from 'framer-motion';

interface CardGridProps {
  children: ReactNode;
  ultraWide?: boolean;
}

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.05,
    },
  },
};

export function CardGrid({ children, ultraWide = false }: CardGridProps) {
  return (
    <motion.div
      className={`bento-grid ${ultraWide ? 'bento-grid-ultrawide' : ''}`}
      variants={containerVariants}
      initial="hidden"
      animate="visible"
    >
      {children}
    </motion.div>
  );
}
