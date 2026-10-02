/**
 * Node sizes and rect helpers for the canvas (W3 review fix EA-13).
 *
 * A leaf module: no imports from the store, React Flow mapping or plugins,
 * so the store slices (store/annotations.ts), rfMapping.ts and the plugins
 * can all use it without forming an import cycle (rfMapping.ts is the file
 * W5 and W6 grow; the day it reads the store, a store slice importing it
 * would close a cycle).
 */

/** Node box used until React Flow has measured a node (BaseNode's default width, header plus one row). */
export const DEFAULT_NODE_SIZE = { w: 176, h: 60 }

export type RectTuple = [number, number, number, number]
export interface XY { x: number; y: number }
export interface Size { w: number; h: number }
/** The size of a graph node on screen, or null when unknown (default size is used). */
export type SizeOf = (nodeId: string) => Size | null

/** Anything that can look up a React Flow node (the React Flow instance). */
export interface NodeLookup {
  getNode(id: string): { measured?: { width?: number; height?: number }; width?: number; height?: number } | undefined
}

/** Node sizes as React Flow measured them (null until measured). */
export function rfSizeOf(rf: NodeLookup): SizeOf {
  return id => {
    const n = rf.getNode(id)
    const w = n?.measured?.width ?? n?.width
    const h = n?.measured?.height ?? n?.height
    return w != null && h != null ? { w, h } : null
  }
}

/** True when the point lies inside the rect (edges included). */
export function rectContains(rect: RectTuple, p: XY): boolean {
  return p.x >= rect[0] && p.x <= rect[0] + rect[2] && p.y >= rect[1] && p.y <= rect[1] + rect[3]
}

/** The center of a graph node, using its on-screen size when known. */
export function nodeCenter(position: [number, number], size: Size | null): XY {
  const s = size ?? DEFAULT_NODE_SIZE
  return { x: position[0] + s.w / 2, y: position[1] + s.h / 2 }
}
