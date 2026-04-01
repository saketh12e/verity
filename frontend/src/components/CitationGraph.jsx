import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'

// Primary = blue (authoritative), secondary = green (established), opinion = gray
const TIER_COLOR = {
  primary: '#3b82f6',
  secondary: '#22c55e',
  opinion: '#9ca3af',
}

// 4-tier badge colors for new schema
const BADGE_COLOR = {
  VERIFIED:           '#22c55e',
  PARTIALLY_VERIFIED: '#eab308',
  UNVERIFIED:         '#f97316',
  UNSUPPORTED:        '#ef4444',
}

const TIER_RADIUS = {
  primary: 10,
  secondary: 7,
  opinion: 5,
}

function getNodeColor(node) {
  // Phase 3: backend sets node_color directly — frontend just reads it
  if (node.node_color) return node.node_color
  // Fallback for old schema nodes: badge_contribution → color
  if (node.badge_contribution && BADGE_COLOR[node.badge_contribution]) {
    return BADGE_COLOR[node.badge_contribution]
  }
  // Old schema: in_loop override
  if (node.in_loop) return '#ef4444'
  // Old schema: tier field
  return TIER_COLOR[node.tier] ?? '#9ca3af'
}

function getNodeRadius(node) {
  return TIER_RADIUS[node.tier] ?? 6
}

function renderGraph(data, svgElement) {
  const width = svgElement.clientWidth || 600
  const height = svgElement.clientHeight || 350

  const svg = d3.select(svgElement)
  svg.selectAll('*').remove()

  // Cloned arrays so D3 force sim can mutate x/y freely
  const nodes = data.nodes.map(n => ({ ...n }))
  const links = data.edges.map(e => ({ ...e }))  // keep source/target as string IDs

  const simulation = d3.forceSimulation(nodes)
    // id(d => d.id) lets D3 resolve source/target by the 'id' string field
    .force('link', d3.forceLink(links).id(d => d.id).distance(80).strength(0.5))
    .force('charge', d3.forceManyBody().strength(-200))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(d => getNodeRadius(d) + 3))

  // Zoom/pan container
  const container = svg.append('g')
  svg.call(
    d3.zoom().scaleExtent([0.25, 5])
      .on('zoom', e => container.attr('transform', e.transform))
  )

  // Arrowhead markers
  const defs = svg.append('defs')
  ;['normal', 'loop', 'contradicts'].forEach(type => {
    defs.append('marker')
      .attr('id', `da-arrow-${type}`)
      .attr('viewBox', '0 -4 10 8')
      .attr('refX', 18).attr('refY', 0)
      .attr('markerWidth', 6).attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L10,0L0,4')
      .attr('fill', (type === 'loop' || type === 'contradicts') ? '#ef4444' : '#4b5563')
  })

  const link = container.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', d => {
      if (d.relationship_type === 'contradicts') return '#ef4444'
      if (d.is_loop) return '#ef4444'
      return '#374151'
    })
    .attr('stroke-width', d => (d.is_loop || d.relationship_type === 'contradicts') ? 2 : 1)
    .attr('stroke-opacity', 0.7)
    .attr('stroke-dasharray', d => d.relationship_type === 'contradicts' ? '5,4' : null)
    .attr('marker-end', d => {
      if (d.relationship_type === 'contradicts') return 'url(#da-arrow-contradicts)'
      if (d.is_loop) return 'url(#da-arrow-loop)'
      return 'url(#da-arrow-normal)'
    })

  const node = container.append('g')
    .selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('r', d => getNodeRadius(d))
    .attr('fill', d => getNodeColor(d))
    // node_border = amber ring for gemini_grounding sources (Phase 3)
    .attr('stroke', d => d.node_border ?? '#0f172a')
    .attr('stroke-width', d => d.node_border ? 3 : 1.5)
    .attr('cursor', 'pointer')
    .call(
      d3.drag()
        .on('start', (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart()
          d.fx = d.x; d.fy = d.y
        })
        .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y })
        .on('end', (event, d) => {
          if (!event.active) simulation.alphaTarget(0)
          d.fx = null; d.fy = null
        })
    )

  // Native SVG title for hover tooltip (no external div needed)
  node.append('title')
    .text(d => {
      const tierOrBadge = d.badge_contribution ?? d.tier ?? 'unknown'
      const domain = d.domain ?? (d.url ? d.url.replace(/^https?:\/\/(www\.)?/, '').split('/')[0] : 'unknown')
      const providerInfo = d.provider ? `\nProvider: ${d.provider}` : ''
      const titleInfo = d.title ? `\n${d.title}` : ''
      const loopWarning = d.in_loop ? '\n⚠ In circular citation loop — confidence penalized' : ''
      const citationsInfo = d.citations_received !== undefined ? `\nCitations received: ${d.citations_received}` : ''
      return `${domain} (${tierOrBadge})${titleInfo}${citationsInfo}${providerInfo}${loopWarning}`
    })

  // Domain labels
  container.append('g')
    .selectAll('text')
    .data(nodes)
    .join('text')
    .attr('font-size', 8)
    .attr('fill', '#6b7280')
    .attr('text-anchor', 'middle')
    .attr('pointer-events', 'none')
    .text(d => {
      const domain = d.domain ?? (d.url ? d.url.replace(/^https?:\/\/(www\.)?/, '').split('/')[0] : '')
      return domain?.replace(/^www\./, '').slice(0, 16) ?? ''
    })

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
    node
      .attr('cx', d => d.x).attr('cy', d => d.y)
    container.selectAll('text')
      .attr('x', (d, i) => nodes[i]?.x ?? 0)
      .attr('y', (d, i) => (nodes[i]?.y ?? 0) - getNodeRadius(nodes[i] ?? {}) - 4)
  })

  return () => simulation.stop()
}

// Normalize new schema edges: map claim_index+source_id pairs to D3 source/target edges
function normalizeGraphData(data) {
  if (!data?.nodes?.length) return data

  // Detect new schema: nodes have badge_contribution OR edges have source_id field
  const isNewSchema = data.nodes.some(n => n.badge_contribution) ||
    (data.edges?.length > 0 && data.edges[0].source_id !== undefined)

  if (!isNewSchema) return data  // old schema — D3-ready already

  // New schema: edges are { claim_index, source_id, relationship_type }
  // Convert to D3-compatible { source, target, relationship_type } by grouping
  // edges by claim and creating pairwise connections between source nodes.
  const claimGroups = {}
  for (const e of (data.edges || [])) {
    if (e.claim_index === undefined) continue  // skip malformed edges
    if (!claimGroups[e.claim_index]) claimGroups[e.claim_index] = []
    claimGroups[e.claim_index].push(e)
  }

  const graphEdges = []
  const edgeSeen = new Set()

  for (const groupEdges of Object.values(claimGroups)) {
    const supports = groupEdges.filter(e => e.relationship_type === 'supports')
    const contradicts = groupEdges.filter(e => e.relationship_type === 'contradicts')

    // Connect consecutive supporting sources with solid edges
    for (let i = 0; i < supports.length - 1; i++) {
      const key = `${supports[i].source_id}--${supports[i + 1].source_id}`
      if (!edgeSeen.has(key)) {
        edgeSeen.add(key)
        graphEdges.push({
          source: supports[i].source_id,
          target: supports[i + 1].source_id,
          relationship_type: 'supports',
          id: key,
        })
      }
    }

    // Connect supporting sources to contradicting sources with dashed edges
    for (const s of supports) {
      for (const c of contradicts) {
        const key = `${s.source_id}--${c.source_id}-contra`
        if (!edgeSeen.has(key)) {
          edgeSeen.add(key)
          graphEdges.push({
            source: s.source_id,
            target: c.source_id,
            relationship_type: 'contradicts',
            id: key,
          })
        }
      }
    }
  }

  return { ...data, edges: graphEdges }
}

export default function CitationGraph({ jobId, isComplete, graphData: graphDataProp }) {
  const svgRef = useRef(null)
  const [fetchedGraphData, setFetchedGraphData] = useState(null)
  const [loading, setLoading] = useState(false)

  // If graphData prop is provided with nodes, use it directly — no fetch needed
  const graphData = (graphDataProp?.nodes?.length > 0)
    ? normalizeGraphData(graphDataProp)
    : fetchedGraphData

  // Only fetch after job is done and no prop data was supplied
  useEffect(() => {
    if (!jobId || !isComplete) return
    if (graphDataProp?.nodes?.length > 0) return  // prop takes priority
    setLoading(true)
    fetch(`/graph/${jobId}`)
      .then(r => r.json())
      .then(data => { setFetchedGraphData(normalizeGraphData(data)); setLoading(false) })
      .catch(() => setLoading(false))
  }, [jobId, isComplete, graphDataProp])

  useEffect(() => {
    if (!graphData?.nodes?.length || !svgRef.current) return
    const cleanup = renderGraph(graphData, svgRef.current)
    return cleanup
  }, [graphData])

  if (!jobId || !isComplete) return null

  const stats = graphData?.stats
  // graph_generated = false means backend built the object but found no relationships
  const graphGenerated = graphData?.graph_generated !== false  // true if field absent (old schema)
  const hasData = graphData?.nodes?.length > 0 && graphGenerated

  // Determine if this is new schema (has badge_contribution)
  const isNewSchema = hasData && graphData.nodes.some(n => n.badge_contribution)

  return (
    <div className="border border-gray-800 rounded-xl overflow-hidden mt-6">
      {/* Header */}
      <div className="px-5 py-3 bg-gray-900/60 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-200">Source Citation Network</span>
          {stats && hasData && (
            <span className="text-xs text-gray-500 font-mono">
              {stats.total_sources} sources · {stats.total_citations} links
              {stats.circular_loops > 0 && (
                <span className="ml-2 text-red-400 font-medium">
                  {stats.circular_loops} loop{stats.circular_loops !== 1 ? 's' : ''} detected
                </span>
              )}
            </span>
          )}
          {!stats && hasData && (
            <span className="text-xs text-gray-500 font-mono">
              {graphData.nodes.length} sources · {graphData.edges?.length ?? 0} links
            </span>
          )}
        </div>
        {hasData && (
          <div className="flex items-center gap-3 text-[10px] text-gray-500">
            {isNewSchema
              ? Object.entries(BADGE_COLOR).map(([badge, color]) => (
                  <span key={badge} className="flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full inline-block" style={{ background: color }} />
                    {badge.replace('_', ' ').toLowerCase()}
                  </span>
                ))
              : Object.entries(TIER_COLOR).map(([tier, color]) => (
                  <span key={tier} className="flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full inline-block" style={{ background: color }} />
                    {tier}
                  </span>
                ))
            }
            {!isNewSchema && (
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full inline-block bg-red-500" />
                loop
              </span>
            )}
            {isNewSchema && (
              <span className="flex items-center gap-1 ml-1 border-l border-gray-700 pl-2">
                <span className="inline-block w-4 border-t-2 border-dashed border-red-400" />
                contradicts
              </span>
            )}
          </div>
        )}
      </div>

      {/* Canvas */}
      <div className="bg-gray-950 relative" style={{ height: 350 }}>
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center text-gray-500 text-sm">
            Building citation graph...
          </div>
        )}
        {!loading && !hasData && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-600 text-sm gap-2 px-8 text-center">
            <span className="text-2xl">🕸️</span>
            <span>
              {graphData?.generation_note
                ? `Citation graph unavailable — ${graphData.generation_note}`
                : 'Citation graph unavailable — insufficient source-claim links found'
              }
            </span>
          </div>
        )}
        {hasData && (
          <svg ref={svgRef} className="w-full h-full" />
        )}
      </div>

      {/* Loop warnings (old schema) */}
      {graphData?.loops?.length > 0 && (
        <div className="px-5 py-3 bg-red-950/20 border-t border-red-900/30">
          <p className="text-xs text-red-400 font-medium mb-1">
            ⚠ Circular citation loops — these sources cite each other, confidence scores penalized
          </p>
          <div className="space-y-1 max-h-16 overflow-y-auto">
            {graphData.loops.slice(0, 4).map((loop, i) => (
              <p key={i} className="text-[10px] text-red-600 font-mono truncate">
                {loop.url_a.replace(/^https?:\/\//, '')} ↔ {loop.url_b.replace(/^https?:\/\//, '')}
              </p>
            ))}
            {graphData.loops.length > 4 && (
              <p className="text-[10px] text-red-700">+{graphData.loops.length - 4} more loops</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
