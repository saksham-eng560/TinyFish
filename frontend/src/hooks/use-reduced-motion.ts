"use client";

import { useEffect, useState } from "react";
import { useReducedMotion } from "framer-motion";

/**
 * framer-motion's useReducedMotion, but false until the component has mounted.
 *
 * The server can't know the visitor's "reduce motion" setting, so it renders the animated markup.
 * Reading the real setting on the first client render would produce different markup and break
 * hydration (React #418), so the static variant is swapped in right after mount instead.
 */
export function useReducedMotionSafe(): boolean {
  const reduce = useReducedMotion();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted && !!reduce;
}
