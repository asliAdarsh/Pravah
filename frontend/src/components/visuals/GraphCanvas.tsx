/**
 * Neighbourhood layout for the knowledge graph, with no layout library.
 *
 * A small force relaxation runs once per subgraph: repulsion between every pair
 * (the subgraph is capped at a few dozen nodes, so the O(n²) pass is trivial),
 * a spring along each edge, and a weak pull toward the centre. Nodes are then
 * clamped inside the viewport and the result is memoised on the node/edge
 * signature, so re-rendering the panel does not re-run the simulation.
 *
 * Rings are drawn by hop distance from the root, colour comes from the node
 * type, and an edge's type label appears when it is selected or hovered — never
 * all at once, which would be unreadable.
 */
import { useMemo, useState } from 'react'
import { cx } from '@/components/ui'
import { useElementWidth } from '@/lib/useElementWidth'
import { humanizeEnum } from '@/lib/format'
import type { GraphEdge, GraphNode, GraphNodeType } from '../../pages/_engines'

/** Node-type → fill/stroke token. Red is reserved for CRITICAL, so no red here. */
export const NODE_FILL: Record<GraphNodeType, string> = {
  Well: 'var(--accent)',
  Formation: 'var(--ok)',
  Event: 'var(--band-high)',
  Hazard: 'var(--band-warning)',
  Intervention: 'var(--fg-muted)',
  Outcome: 'var(--chrome-2)',
  ReportSnippet: 'var(--band-info)',
}

export const NODE_TYPE_ORDER: GraphNodeType[] = [
  'Well',
  'Event',
  'Hazard',
  'Formation',
  'Intervention',
  'Outcome',
  'ReportSnippet',
]

const HEIGHT = 420
const ROOT_R = 11
const NODE_R = 7
const ITERATIONS = 320
const REPULSION = 5200
const SPRING = 0.045
const SPRING_LENGTH = 78
const CENTER_PULL = 0.012
const DAMPING = 0.82

interface Placed {
  id: string
  node: GraphNode
  x: number
  y: number
  vx: number
  vy: number
  ring: number
}

function signatureOf(nodes: GraphNode[], edges: GraphEdge[]): string {
  return `${nodes.map((node) => node.id).join('|')}::${edges
    .map((edge) => `${edge.source}>${edge.target}`)
    .join('|')}`
}

/**
 * Breadth-first hop distance from the root, following edges in both directions
 * so a node reached only by an incoming edge still gets a ring.
 */
function hopRings(rootId: string, edges: GraphEdge[]): Map<string, number> {
  const neighbours = new Map<string, Set<string>>()
  for (const edge of edges) {
    if (!neighbours.has(edge.source)) neighbours.set(edge.source, new Set())
    if (!neighbours.has(edge.target)) neighbours.set(edge.target, new Set())
    neighbours.get(edge.source)!.add(edge.target)
    neighbours.get(edge.target)!.add(edge.source)
  }
  const rings = new Map<string, number>([[rootId, 0]])
  let frontier = [rootId]
  for (let depth = 1; frontier.length > 0 && depth <= 4; depth += 1) {
    const next: string[] = []
    for (const id of frontier) {
      for (const neighbour of neighbours.get(id) ?? []) {
        if (rings.has(neighbour)) continue
        rings.set(neighbour, depth)
        next.push(neighbour)
      }
    }
    frontier = next
  }
  return rings
}

export function GraphCanvas({
  nodes,
  edges,
  rootId,
  selectedId,
  onSelect,
  className,
}: {
  nodes: GraphNode[]
  edges: GraphEdge[]
  rootId: string
  selectedId: string | null
  onSelect: (node: GraphNode) => void
  className?: string
}) {
  const { ref, width } = useElementWidth<HTMLDivElement>(760)
  const [hoverEdge, setHoverEdge] = useState<GraphEdge | null>(null)
  const signature = useMemo(() => signatureOf(nodes, edges), [nodes, edges])

  const placed = useMemo(() => {
    if (nodes.length === 0) return []
    const widthNow = Math.max(width, 320)
    const cxPos = widthNow / 2
    const cyPos = HEIGHT / 2
    const rings = hopRings(rootId, edges)
    const degrees = new Map<string, number>()
    for (const edge of edges) {
      degrees.set(edge.source, (degrees.get(edge.source) ?? 0) + 1)
      degrees.set(edge.target, (degrees.get(edge.target) ?? 0) + 1)
    }

    /* Deterministic start: golden-angle spiral, so two runs of the same
       subgraph land in the same place and the panel does not jump on re-render. */
    const points: Placed[] = nodes.map((node, index) => {
      const angle = index * 2.39996
      const radius = 26 + 15 * Math.sqrt(index)
      return {
        id: node.id,
        node,
        x: cxPos + radius * Math.cos(angle),
        y: cyPos + radius * Math.sin(angle),
        vx: 0,
        vy: 0,
        ring: rings.get(node.id) ?? 4,
      }
    })
    const byId = new Map(points.map((point) => [point.id, point]))

    for (let step = 0; step < ITERATIONS; step += 1) {
      for (let i = 0; i < points.length; i += 1) {
        for (let j = i + 1; j < points.length; j += 1) {
          const a = points[i]
          const b = points[j]
          let dx = b.x - a.x
          let dy = b.y - a.y
          let distanceSq = dx * dx + dy * dy
          if (distanceSq < 1) {
            dx = (i % 2 === 0 ? 1 : -1) * 0.7
            dy = 0.7
            distanceSq = 1
          }
          const distance = Math.sqrt(distanceSq)
          const force = REPULSION / distanceSq
          const fx = (dx / distance) * force
          const fy = (dy / distance) * force
          a.vx -= fx
          a.vy -= fy
          b.vx += fx
          b.vy += fy
        }
      }
      for (const edge of edges) {
        const a = byId.get(edge.source)
        const b = byId.get(edge.target)
        if (!a || !b) continue
        const dx = b.x - a.x
        const dy = b.y - a.y
        const distance = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01)
        const force = (distance - SPRING_LENGTH) * SPRING
        const fx = (dx / distance) * force
        const fy = (dy / distance) * force
        a.vx += fx
        a.vy += fy
        b.vx -= fx
        b.vy -= fy
      }
      for (const point of points) {
        if (point.id === rootId) continue
        point.vx += (cxPos - point.x) * CENTER_PULL
        point.vy += (cyPos - point.y) * CENTER_PULL
      }
      for (const point of points) {
        if (point.id === rootId) {
          point.x = cxPos
          point.y = cyPos
          point.vx = 0
          point.vy = 0
          continue
        }
        point.x += (point.vx *= DAMPING)
        point.y += (point.vy *= DAMPING)
        point.x = Math.min(widthNow - 26, Math.max(26, point.x))
        point.y = Math.min(HEIGHT - 20, Math.max(20, point.y))
      }
    }
    return points
    // `width` is deliberately excluded: the simulation is memoised on the
    // subgraph signature so it does not re-run on every resize tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, rootId])

  const byId = useMemo(() => new Map(placed.map((point) => [point.id, point])), [placed])
  const activeEdges = useMemo(
    () => edges.filter((edge) => byId.has(edge.source) && byId.has(edge.target)),
    [edges, byId],
  )
  const focusId = hoverEdge ? (selectedId ?? rootId) : selectedId
  const ringCounts = useMemo(() => {
    const counts = new Map<number, number>()
    for (const point of placed) counts.set(point.ring, (counts.get(point.ring) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => a[0] - b[0])
  }, [placed])

  return (
    <div ref={ref} className={cx('min-w-0', className)}>
      <div className="plot-surface overflow-hidden">
        <svg width={Math.max(width, 320)} height={HEIGHT} role="img" aria-label={`Force layout of ${nodes.length} graph nodes and ${edges.length} edges, root ${rootId}`}>
          {/* Hop rings: a reference frame, not decoration. */}
          {ringCounts.map(([ring]) =>
            ring === 0 ? null : (
              <circle
                key={ring}
                cx={Math.max(width, 320) / 2}
                cy={HEIGHT / 2}
                r={ring * 78}
                fill="none"
                stroke="var(--grid)"
                strokeWidth={1}
                strokeDasharray="3 5"
              />
            ),
          )}

          {activeEdges.map((edge, index) => {
            const a = byId.get(edge.source)!
            const b = byId.get(edge.target)!
            const highlighted =
              hoverEdge === edge ||
              (focusId !== null && (edge.source === focusId || edge.target === focusId))
            return (
              <line
                key={`${edge.source}-${edge.target}-${index}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={highlighted ? 'var(--accent)' : 'var(--line-strong)'}
                strokeWidth={highlighted ? 1.6 : 1}
                opacity={focusId === null || highlighted ? 1 : 0.28}
                onMouseEnter={() => setHoverEdge(edge)}
                onMouseLeave={() => setHoverEdge(null)}
              />
            )
          })}

          {placed.map((point) => {
            const isRoot = point.id === rootId
            const isSelected = point.id === selectedId
            const dimmed = focusId !== null && point.id !== focusId && !isRoot
            const radius = isRoot ? ROOT_R : NODE_R
            return (
              <g
                key={point.id}
                transform={`translate(${point.x} ${point.y})`}
                opacity={dimmed ? 0.35 : 1}
                className="cursor-pointer"
                onClick={() => onSelect(point.node)}
              >
                <title>{`${point.node.label} · ${humanizeEnum(point.node.type)} · ${point.ring} hop${point.ring === 1 ? '' : 's'} from root`}</title>
                {isSelected ? (
                  <circle r={radius + 4} fill="none" stroke="var(--accent)" strokeWidth={1.5} />
                ) : null}
                <circle
                  r={radius}
                  fill={NODE_FILL[point.node.type] ?? 'var(--fg-muted)'}
                  stroke="var(--plot-bg)"
                  strokeWidth={1.5}
                />
                {isRoot || isSelected || point.ring === 1 ? (
                  <text
                    x={radius + 5}
                    y={3.5}
                    fontSize={10}
                    fill="var(--fg-muted)"
                    style={{ pointerEvents: 'none' }}
                  >
                    {point.node.label.length > 26
                      ? `${point.node.label.slice(0, 25)}…`
                      : point.node.label}
                  </text>
                ) : null}
              </g>
            )
          })}
        </svg>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {NODE_TYPE_ORDER.filter((type) => nodes.some((node) => node.type === type)).map(
          (type) => (
            <span key={type} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block size-2 shrink-0 rounded-full"
                style={{ background: NODE_FILL[type] }}
              />
              <span className="text-2xs text-fg-muted">{humanizeEnum(type)}</span>
            </span>
          ),
        )}
        <span className="text-2xs text-fg-subtle">
          {hoverEdge
            ? `${humanizeEnum(hoverEdge.type)} · ${byId.get(hoverEdge.source)?.node.label} → ${byId.get(hoverEdge.target)?.node.label}`
            : 'Hover an edge for its relation type, click a node to inspect it.'}
        </span>
      </div>
    </div>
  )
}
