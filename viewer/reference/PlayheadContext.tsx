'use client'

import { createContext, useContext, useRef, type ReactNode } from 'react'

type PlayheadListener = (ms: number | null) => void

export interface PlayheadCtx {
  /** Emit session-relative telemetry time in milliseconds. */
  setPlayheadMs: (ms: number | null) => void
  /** Subscribe to playhead changes. Returns cleanup fn. */
  subscribe: (fn: PlayheadListener) => () => void
}

const PlayheadContext = createContext<PlayheadCtx | null>(null)

export function PlayheadProvider({ children }: { children: ReactNode }) {
  const listeners = useRef<Set<PlayheadListener>>(new Set())

  // Stable object, matching CurveHoverContext, so playback does not re-render the tree.
  const value = useRef<PlayheadCtx>({
    setPlayheadMs(ms) {
      listeners.current.forEach(fn => fn(ms))
    },
    subscribe(fn) {
      listeners.current.add(fn)
      return () => listeners.current.delete(fn)
    },
  }).current

  return (
    <PlayheadContext.Provider value={value}>
      {children}
    </PlayheadContext.Provider>
  )
}

export function usePlayhead(): PlayheadCtx | null {
  return useContext(PlayheadContext)
}
