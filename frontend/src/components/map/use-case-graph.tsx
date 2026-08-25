'use client'
import dynamic from 'next/dynamic'
import { useMemo, useCallback, useRef, useEffect } from 'react'
import type { UseCase, Person } from '@/lib/aimaps-types'

const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), { ssr: false })

// ── Types ──────────────────────────────────────────────────────────
interface GraphNode {
  id: string
  label: string
  sublabel?: string
  type: 'usecase' | 'model' | 'vendor' | 'owner' | 'risk'
  severity?: string
  stat?: string
  color: string
  borderColor: string
  data?: UseCase
  x?: number
  y?: number
  vx?: number
  vy?: number
}

interface GraphLink {
  source: string
  target: string
  linkType: 'uses' | 'provided-by' | 'owned-by' | 'has-risk'
  color: string
}

// ── Visual config — matches reference governance dashboard palette ──
const NODE_CFG = {
  usecase: { bg: '#141824', border: '#00d9ff', label: 'Use Case' },
  model:   { bg: '#141824', border: '#f59e0b', label: 'AI Model' },
  vendor:  { bg: '#141824', border: '#10b981', label: 'Vendor'   },
  owner:   { bg: '#141824', border: '#7c3aed', label: 'Owner'    },
  risk:    { bg: '#141824', border: '#ef4444', label: 'Risk'     },
} as const

const SEVERITY_BORDER: Record<string, string> = {
  critical: '#dc2626',
  high:     '#f97316',
  medium:   '#eab308',
  low:      '#22c55e',
}

const LINK_CFG = {
  'uses':         { color: '#00d9ff40', dash: false },
  'provided-by':  { color: '#10b98140', dash: true  },
  'owned-by':     { color: '#7c3aed40', dash: false },
  'has-risk':     { color: '#ef444440', dash: true  },
} as const

// Card size in graph-units (not pixels — ForceGraph handles scaling)
const CW = 110
const CH = 42
const CR = 5

interface UseCaseGraphProps {
  useCases: UseCase[]
  persons: Person[]
  filterDept?: string
  filterSeverity?: string
  onSelectUseCase: (uc: UseCase) => void
}

export function UseCaseGraph({ useCases, persons, filterDept, filterSeverity, onSelectUseCase }: UseCaseGraphProps) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const graphRef = useRef<any>(null)

  const filtered = useMemo(() =>
    useCases.filter(uc =>
      (!filterDept     || filterDept     === 'all' || uc.department === filterDept) &&
      (!filterSeverity || filterSeverity === 'all' || uc.risks.some(r => r.severity === filterSeverity))
    ), [useCases, filterDept, filterSeverity])

  const { nodes, links } = useMemo(() => {
    const nodes: GraphNode[] = []
    const links: GraphLink[] = []
    const seenModels  = new Set<string>()
    const seenVendors = new Set<string>()
    const seenOwners  = new Set<string>()

    filtered.forEach(uc => {
      nodes.push({
        id: uc.id, label: uc.name, sublabel: uc.department, type: 'usecase',
        stat: `${uc.risks.length} risk${uc.risks.length !== 1 ? 's' : ''}`,
        color: NODE_CFG.usecase.border, borderColor: NODE_CFG.usecase.border, data: uc,
      })

      uc.models.forEach(m => {
        const vid = `vendor-${m.provider}`
        if (!seenVendors.has(vid)) {
          nodes.push({ id: vid, label: m.provider, sublabel: 'Vendor', type: 'vendor', stat: 'External API', color: NODE_CFG.vendor.border, borderColor: NODE_CFG.vendor.border })
          seenVendors.add(vid)
        }
        if (!seenModels.has(m.id)) {
          nodes.push({ id: m.id, label: m.name, sublabel: m.type.toUpperCase(), type: 'model', stat: m.provider, color: NODE_CFG.model.border, borderColor: NODE_CFG.model.border })
          seenModels.add(m.id)
          links.push({ source: m.id, target: vid, linkType: 'provided-by', color: LINK_CFG['provided-by'].color })
        }
        links.push({ source: uc.id, target: m.id, linkType: 'uses', color: LINK_CFG['uses'].color })
      })

      if (uc.ownerId) {
        const owner = persons.find(p => p.id === uc.ownerId)
        if (owner) {
          if (!seenOwners.has(owner.id)) {
            nodes.push({ id: owner.id, label: owner.name, sublabel: owner.department, type: 'owner', stat: owner.role ?? '', color: NODE_CFG.owner.border, borderColor: NODE_CFG.owner.border })
            seenOwners.add(owner.id)
          }
          links.push({ source: uc.id, target: owner.id, linkType: 'owned-by', color: LINK_CFG['owned-by'].color })
        }
      }

      // Only show worst risk per use case to reduce clutter
      const worstRisk = uc.risks.slice().sort((a, b) => {
        const order = { critical: 0, high: 1, medium: 2, low: 3 }
        return (order[a.severity as keyof typeof order] ?? 9) - (order[b.severity as keyof typeof order] ?? 9)
      })[0]
      if (worstRisk) {
        const rid    = `${uc.id}-${worstRisk.id}`
        const border = SEVERITY_BORDER[worstRisk.severity] ?? NODE_CFG.risk.border
        nodes.push({ id: rid, label: worstRisk.name, sublabel: worstRisk.severity.toUpperCase(), type: 'risk', severity: worstRisk.severity, stat: 'Risk', color: border, borderColor: border })
        links.push({ source: uc.id, target: rid, linkType: 'has-risk', color: LINK_CFG['has-risk'].color })
      }
    })

    return { nodes, links }
  }, [filtered, persons])

  // Department → radial position mapping for clustering
  const deptPositions = useMemo(() => {
    const depts = Array.from(new Set(filtered.map(uc => uc.department)))
    const r = Math.max(200, depts.length * 55)
    return Object.fromEntries(depts.map((d, i) => {
      const angle = (2 * Math.PI * i) / depts.length - Math.PI / 2
      return [d, { x: Math.cos(angle) * r, y: Math.sin(angle) * r }]
    }))
  }, [filtered])

  // Configure d3 forces + department clustering + zoom-to-fit
  useEffect(() => {
    const t = setTimeout(() => {
      const fg = graphRef.current
      if (!fg) return
      fg.d3Force('charge')?.strength(-400)
      fg.d3Force('link')?.distance(80).strength(0.5)
      const collide = Math.sqrt((CW / 2) ** 2 + (CH / 2) ** 2) + 8
      fg.d3Force('collision')?.radius(collide)
      // Cluster use-case nodes toward their department centroid
      const clusterX = fg.d3Force('x') ?? { strength: () => {} }
      const clusterY = fg.d3Force('y') ?? { strength: () => {} }
      if (fg.d3Force('x')) {
        fg.d3Force('x')
          .strength((n: GraphNode) => n.type === 'usecase' ? 0.15 : 0.05)
          .x((n: GraphNode) => {
            if (n.type === 'usecase' && n.data) return deptPositions[n.data.department]?.x ?? 0
            return 0
          })
        fg.d3Force('y')
          .strength((n: GraphNode) => n.type === 'usecase' ? 0.15 : 0.05)
          .y((n: GraphNode) => {
            if (n.type === 'usecase' && n.data) return deptPositions[n.data.department]?.y ?? 0
            return 0
          })
      }
      void clusterX; void clusterY
      fg.d3ReheatSimulation?.()
      setTimeout(() => fg.zoomToFit?.(600, 60), 2500)
    }, 100)
    return () => clearTimeout(t)
  }, [filtered, deptPositions])

  const handleNodeClick = useCallback((node: object) => {
    const n = node as GraphNode
    if (n.type === 'usecase' && n.data) onSelectUseCase(n.data)
  }, [onSelectUseCase])

  // ── Card painter ────────────────────────────────────────────────
  const paintNode = useCallback((node: object, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const n   = node as GraphNode
    const cx  = n.x ?? 0
    const cy  = n.y ?? 0
    const cfg = NODE_CFG[n.type]

    // At very low zoom, draw a simple dot — keep canvas fast
    if (globalScale < 0.35) {
      ctx.beginPath()
      ctx.arc(cx, cy, 4, 0, 2 * Math.PI)
      ctx.fillStyle = n.borderColor
      ctx.fill()
      return
    }

    const x = cx - CW / 2
    const y = cy - CH / 2

    ctx.save()

    // Card shadow on use cases only
    if (n.type === 'usecase') {
      ctx.shadowColor = `${n.borderColor}33`
      ctx.shadowBlur  = 12
    }

    // Background
    ctx.fillStyle = cfg.bg
    rr(ctx, x, y, CW, CH, CR)
    ctx.fill()
    ctx.shadowBlur = 0

    // Left accent bar
    ctx.fillStyle = n.borderColor
    rr(ctx, x, y, 3, CH, CR)
    ctx.fill()

    // Border
    ctx.strokeStyle = `${n.borderColor}55`
    ctx.lineWidth   = 0.8
    rr(ctx, x, y, CW, CH, CR)
    ctx.stroke()

    // Use-case: glow border ring
    if (n.type === 'usecase') {
      ctx.strokeStyle = `${n.borderColor}22`
      ctx.lineWidth   = 2
      rr(ctx, x - 1, y - 1, CW + 2, CH + 2, CR + 1)
      ctx.stroke()
    }

    // ── Text content ────────────────────────────────────────────
    const textX = x + 7

    // Type badge
    if (globalScale >= 0.45) {
      const bfs  = Math.max(3, 3.5)
      ctx.font   = `bold ${bfs}px -apple-system,sans-serif`
      const btxt = cfg.label.toUpperCase()
      const bw   = ctx.measureText(btxt).width + 6
      const bh   = bfs + 4
      const bx   = x + CW - bw - 3
      const by   = y + 3
      ctx.fillStyle = `${n.borderColor}25`
      rr(ctx, bx, by, bw, bh, 1.5)
      ctx.fill()
      ctx.fillStyle    = n.borderColor
      ctx.textAlign    = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(btxt, bx + bw / 2, by + bh / 2)
    }

    // Title
    const tfs      = 5.5
    const maxChars = n.type === 'usecase' ? 15 : 13
    const title    = n.label.length > maxChars ? n.label.slice(0, maxChars - 1) + '…' : n.label
    ctx.font         = `bold ${tfs}px -apple-system,sans-serif`
    ctx.fillStyle    = '#e8edf4'
    ctx.textAlign    = 'left'
    ctx.textBaseline = 'top'
    ctx.fillText(title, textX, y + CH * 0.28)

    // Sublabel
    if (n.sublabel && globalScale >= 0.5) {
      ctx.font      = `4px -apple-system,sans-serif`
      ctx.fillStyle = '#6b7689'
      ctx.fillText(n.sublabel, textX, y + CH * 0.28 + tfs + 2)
    }

    // Stat line
    if (n.stat && globalScale >= 0.6) {
      ctx.font         = `3.5px monospace`
      ctx.fillStyle    = n.borderColor
      ctx.textBaseline = 'bottom'
      ctx.fillText(n.stat, textX, y + CH - 3)
    }

    ctx.restore()
  }, [])

  // ── Link painter with label ─────────────────────────────────────
  const paintLink = useCallback((link: object, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const l   = link as GraphLink & { source: GraphNode; target: GraphNode }
    const cfg = LINK_CFG[l.linkType]
    const sx  = l.source.x ?? 0
    const sy  = l.source.y ?? 0
    const tx  = l.target.x ?? 0
    const ty  = l.target.y ?? 0

    ctx.save()
    ctx.beginPath()
    ctx.moveTo(sx, sy)
    ctx.lineTo(tx, ty)
    ctx.strokeStyle = cfg.color
    ctx.lineWidth   = 1
    if (cfg.dash) ctx.setLineDash([4, 3])
    ctx.stroke()
    ctx.setLineDash([])

    // Relationship label at midpoint (only when zoomed in)
    if (globalScale >= 0.8) {
      const mx = (sx + tx) / 2
      const my = (sy + ty) / 2
      const labels: Record<GraphLink['linkType'], string> = {
        'uses': 'uses', 'provided-by': 'via', 'owned-by': 'owner', 'has-risk': 'risk'
      }
      ctx.font      = '3.5px -apple-system,sans-serif'
      ctx.fillStyle = cfg.color.replace('40', 'aa')
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(labels[l.linkType], mx, my - 3)
    }

    ctx.restore()
  }, [])

  // ── Hit area = full card rect ───────────────────────────────────
  const paintPointerArea = useCallback((node: object, color: string, ctx: CanvasRenderingContext2D) => {
    const n = node as GraphNode
    ctx.fillStyle = color
    ctx.fillRect((n.x ?? 0) - CW / 2, (n.y ?? 0) - CH / 2, CW, CH)
  }, [])

  return (
    <div className="w-full h-full rounded-xl overflow-hidden relative" style={{ background: '#0a0e1a' }}>
      {/* Subtle grid background */}
      <div className="absolute inset-0 pointer-events-none" style={{
        backgroundImage: 'linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)',
        backgroundSize: '40px 40px',
      }} />

      <ForceGraph2D
        ref={graphRef}
        graphData={{ nodes, links } as never}
        nodeLabel={(n: object) => {
          const node = n as GraphNode
          return `${NODE_CFG[node.type].label}: ${node.label}${node.sublabel ? ` · ${node.sublabel}` : ''}`
        }}
        nodeVal={20}
        nodeColor={(n: object) => (n as GraphNode).color}
        nodeCanvasObject={paintNode}
        nodeCanvasObjectMode={() => 'replace'}
        nodePointerAreaPaint={paintPointerArea}
        linkColor={(l: object) => (l as GraphLink).color}
        linkCanvasObject={paintLink}
        linkCanvasObjectMode={() => 'replace'}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        linkDirectionalArrowColor={(l: object) => (l as GraphLink).color}
        onNodeClick={handleNodeClick}
        backgroundColor="rgba(0,0,0,0)"
        cooldownTicks={200}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.4}
        enableNodeDrag={true}
        enableZoomInteraction={true}
        minZoom={0.2}
        maxZoom={5}
      />
    </div>
  )
}

// ── Rounded rect helper ───────────────────────────────────────────
function rr(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const safe = Math.min(r, w / 2, h / 2)
  ctx.beginPath()
  ctx.moveTo(x + safe, y)
  ctx.lineTo(x + w - safe, y)
  ctx.arcTo(x + w, y, x + w, y + safe, safe)
  ctx.lineTo(x + w, y + h - safe)
  ctx.arcTo(x + w, y + h, x + w - safe, y + h, safe)
  ctx.lineTo(x + safe, y + h)
  ctx.arcTo(x, y + h, x, y + h - safe, safe)
  ctx.lineTo(x, y + safe)
  ctx.arcTo(x, y, x + safe, y, safe)
  ctx.closePath()
}
