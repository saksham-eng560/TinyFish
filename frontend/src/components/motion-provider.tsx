"use client";

import { MotionConfig, motion } from "framer-motion";
import { EASE_OUT } from "@/components/motion";
import { useReducedMotionSafe } from "@/hooks/use-reduced-motion";

/** Framer Motion follows the OS "reduce motion" setting everywhere. */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}

/** Soft cross-fade between top-level sections (landing, sign-in, dashboard). */
export function RootFade({ children }: { children: React.ReactNode }) {
  const reduce = useReducedMotionSafe();
  return (
    <motion.div initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.35, ease: EASE_OUT }}>
      {children}
    </motion.div>
  );
}
