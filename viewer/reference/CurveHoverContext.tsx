'use client'

import { createContext, useContext, useRef, type ReactNode } from 'react'

type HoverListener = (index: number | null) => void

export interface CurveHoverCtx {
  /** Emit a hover index (0-199 stroke %) to all other charts in the group */
  setHoverIndex: (index: number | null) => void
  /** Subscribe to hover index events from other charts. Returns cleanup fn. */
  subscribe: (fn: HoverListener) => () => void
}

const CurveHoverContext = createContext<CurveHoverCtx | null>(null)

export function CurveHoverProvider({ children }: { children: ReactNode }) {
  const listeners = useRef<Set<HoverListener>>(new Set())

  // Stable object, never changes, so no Provider value re-render on hover
  const value = useRef<CurveHoverCtx>({
    setHoverIndex(index) {
      listeners.current.forEach(fn => fn(index))
    },
    subscribe(fn) {
      listeners.current.add(fn)
      return () => listeners.current.delete(fn)
    },
  }).current

  return (
    <CurveHoverContext.Provider value={value}>
      {children}
    </CurveHoverContext.Provider>
  )
}

export function useCurveHover(): CurveHoverCtx | null {
  return useContext(CurveHoverContext)
}
