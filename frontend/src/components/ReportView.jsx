import { useState } from 'react'
import { ConfidenceBadge } from './ConfidenceBadge.jsx'
import CitationGraph from './CitationGraph.jsx'

// Parse inline [VERDICT] and [Domain, Year] tags into renderable segments
function parseContent(text, sourcesMap) {
  const regex = /(\[VERIFIED\]|\[CONTESTED\]|\[UNVERIFIED\]|\[PARTIALLY_VERIFIED\]|\[UNSUPPORTED\]|\[[^\]]+,\s*\d{4}\]|\[[^\]]+,\s*unknown\])/g
  const parts = []
  let last = 0
  let match

  while ((match = regex.exec(text)) !== null) {
    if (match.index > last) {
      parts.push({ type: 'text', value: text.slice(last, match.index) })
    }
    const token = match[1]
    if (
      token === '[VERIFIED]' || token === '[CONTESTED]' || token === '[UNVERIFIED]' ||
      token === '[PARTIALLY_VERIFIED]' || token === '[UNSUPPORTED]'
    ) {
      parts.push({ type: 'badge', verdict: token.slice(1, -1) })
    } else {
      // [Domain, Year] citation — look up URL from sourcesMap
      const label = token.slice(1, -1)  // e.g. "Reuters, 2024"
      const url = sourcesMap?.[label.toLowerCase().trim()]
      parts.push({ type: 'cite', label, url })
    }
    last = match.index + match[0].length
  }
  if (last < text.length) {
    parts.push({ type: 'text', value: text.slice(last) })
  }
  return parts
}

function buildSourcesMap(sources) {
  if (!sources?.length) return {}
  const map = {}
  for (const s of sources) {
    const key = `${s.domain}, ${s.year}`.toLowerCase().trim()
    map[key] = s.url
  }
  return map
}

function RichContent({ text, sources }) {
  const sourcesMap = buildSourcesMap(sources)
  const parts = parseContent(text, sourcesMap)
  return (
    <span>
      {parts.map((p, i) => {
        if (p.type === 'badge') return <ConfidenceBadge key={i} verdict={p.verdict} />
        if (p.type === 'cite') {
          return p.url
            ? <a key={i} href={p.url} target="_blank" rel="noreferrer"
                 className="text-blue-400 hover:text-blue-300 underline underline-offset-2 text-[0.85em]">
                [{p.label}]
              </a>
            : <span key={i} className="text-blue-400 text-[0.85em]">[{p.label}]</span>
        }
        return <span key={i}>{p.value}</span>
      })}
    </span>
  )
}

// Always-visible contested debate card
function ConflictDebateCard({ conflicts }) {
  if (!conflicts?.length) return null
  return (
    <div className="my-5 space-y-3">
      {conflicts.map((c, i) => (
        <div
          key={i}
          className="border-l-4 border-yellow-600 pl-4 py-1"
        >
          <div className="flex items-center gap-2 mb-2">
            <span className="text-yellow-500 font-mono text-xs font-bold">⚡ CONTESTED</span>
            {c.conflict_type && (
              <span className="text-yellow-600/70 text-xs font-mono">
                {c.conflict_type.replace(/_/g, ' ')}
              </span>
            )}
          </div>
          <p className="text-sm text-gray-300 leading-relaxed mb-1.5">
            <span className="text-blue-400 font-semibold text-xs uppercase tracking-wide mr-1.5">Source A</span>
            {c.claim_a}
            {c.source_a && (
              <a href={c.source_a} target="_blank" rel="noreferrer"
                 className="ml-1.5 text-blue-500 hover:text-blue-400 text-[10px] font-mono">
                [src ↗]
              </a>
            )}
          </p>
          <p className="text-sm text-gray-300 leading-relaxed">
            <span className="text-orange-400 font-semibold text-xs uppercase tracking-wide mr-1.5">Source B</span>
            {c.claim_b}
            {c.source_b && (
              <a href={c.source_b} target="_blank" rel="noreferrer"
                 className="ml-1.5 text-blue-500 hover:text-blue-400 text-[10px] font-mono">
                [src ↗]
              </a>
            )}
          </p>
          {c.explanation && (
            <p className="text-xs text-yellow-500/70 italic mt-2">
              {c.explanation}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}

function Section({ section, conflictMap }) {
  const sectionConflicts = (section.claims_referenced || [])
    .flatMap(id => conflictMap[id] || [])

  return (
    <section className="mb-8">
      <h2 className="text-lg font-semibold text-white mb-4 pb-2 border-b border-gray-800"
          style={{ fontFamily: 'Georgia, serif' }}>
        {section.heading}
      </h2>
      <p className="text-[15px] text-gray-200 leading-[1.85]"
         style={{ fontFamily: "'Lora', Georgia, serif" }}>
        <RichContent text={section.content} sources={section.sources} />
      </p>
      {sectionConflicts.length > 0 && (
        <ConflictDebateCard conflicts={sectionConflicts} />
      )}
    </section>
  )
}

// Safe URL hostname extraction
function safeHostname(url) {
  try {
    return new URL(url).hostname.replace('www.', '')
  } catch {
    return url
  }
}

function RecencyBadge({ hours }) {
  if (!hours) return null
  const label = hours <= 24 ? `Last ${hours}h` : hours <= 168 ? 'Last week' : 'Last 12 months'
  const color = hours <= 24 ? 'text-orange-400 border-orange-700' : 'text-blue-400 border-blue-700'
  return (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${color}`}>
      ⏱ {label}
    </span>
  )
}

function ClaimsList({ claims }) {
  if (!claims?.length) return null
  return (
    <div className="mb-8">
      <h3 className="text-xs font-mono font-bold text-gray-500 uppercase tracking-widest mb-4">
        Claims · Source Evidence
      </h3>
      <div className="space-y-3">
        {claims.map((c, i) => (
          <div key={i} className="border border-gray-800 rounded-lg p-4 bg-gray-900/30">
            <div className="flex items-start gap-3">
              <ConfidenceBadge verdict={c.badge} />
              <p className="text-sm text-gray-200 leading-relaxed flex-1">{c.claim}</p>
            </div>
            {(c.sources?.length > 0 || c.contradicting_sources?.length > 0) && (
              <div className="mt-3 pt-3 border-t border-gray-800 flex flex-wrap gap-3 text-xs">
                {c.sources?.map((url, j) => (
                  <a key={j} href={url} target="_blank" rel="noreferrer"
                     className="text-blue-400 hover:text-blue-300 truncate max-w-[200px]">
                    ↗ {safeHostname(url)}
                  </a>
                ))}
                {c.contradicting_sources?.map((url, j) => (
                  <a key={`c${j}`} href={url} target="_blank" rel="noreferrer"
                     className="text-red-400 hover:text-red-300 truncate max-w-[200px]">
                    ✗ {safeHostname(url)}
                  </a>
                ))}
              </div>
            )}
            {c.conflict_note && (
              <p className="mt-2 text-xs text-yellow-500/80 italic">{c.conflict_note}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function ReportView({ result, jobId }) {
  const { report, conflict_reports } = result
  const [downloading, setDownloading] = useState(false)
  if (!report) return null

  // Build map: finding_id → conflicts array
  const conflictMap = {}
  for (const cr of conflict_reports || []) {
    if (cr.conflicts?.length > 0) {
      conflictMap[cr.finding_id] = cr.conflicts
    }
  }

  // Source counts — prefer new schema fields over legacy total_sources
  const sourcesCrawled   = report.sources_crawled  ?? report.total_sources ?? 0
  const sourcesAfterDedup = report.sources_after_dedup ?? null
  const sourcesUsed      = report.sources_used      ?? report.total_sources ?? 0

  // Badge counts — all 4 tiers
  const claimsVerified           = report.verified_count             || 0
  const claimsPartiallyVerified  = report.claims_partially_verified  ?? report.contested_count ?? 0
  const claimsUnverified         = report.unverified_count           || 0
  const claimsUnsupported        = report.claims_unsupported         || 0

  // executive_summary takes priority over summary
  const executiveSummary = report.executive_summary || report.summary

  async function handlePdfDownload() {
    if (!jobId) return
    setDownloading(true)
    try {
      const res = await fetch(`/export/${jobId}/pdf`)
      if (!res.ok) throw new Error(`Export failed: ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `verity-report-${jobId.slice(0, 8)}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="max-w-[720px]">
      {/* Report header */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-mono text-gray-500 uppercase tracking-widest">
            Verity Research Report
          </span>
          <div className="flex items-center gap-3">
            {report.generated_at && (
              <span className="text-xs text-gray-600 font-mono">
                {new Date(report.generated_at).toLocaleString()}
              </span>
            )}
            {jobId && (
              <button
                onClick={handlePdfDownload}
                disabled={downloading}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {downloading ? '⏳' : '⬇'} {downloading ? 'Generating...' : 'Export PDF'}
              </button>
            )}
          </div>
        </div>

        <h1 className="text-2xl font-bold text-white leading-tight mb-4"
            style={{ fontFamily: 'Georgia, serif' }}>
          {report.title}
        </h1>

        {/* Stats — two rows */}
        <div className="mb-4 space-y-2">
          {/* Source count line — spec format: "N crawled · N used · N verified" */}
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500 font-mono">
            <span>
              {sourcesCrawled} sources crawled
              {sourcesAfterDedup != null && <> · {sourcesAfterDedup} after dedup</>}
              {' · '}{sourcesUsed} sources used
              {' · '}{claimsVerified} verified
            </span>
            {report.low_coverage && (
              <span className="text-yellow-600 border border-yellow-800 px-1.5 py-0.5 rounded text-[10px]">
                ⚠ low coverage
              </span>
            )}
            {report.query_recency_window_hours != null && (
              <RecencyBadge hours={report.query_recency_window_hours} />
            )}
          </div>
          {/* All 4 badge tier counts — always shown including zeros */}
          <div className="flex flex-wrap gap-2">
            <ConfidenceBadge verdict="VERIFIED"           count={claimsVerified} />
            <ConfidenceBadge verdict="PARTIALLY_VERIFIED" count={claimsPartiallyVerified} />
            <ConfidenceBadge verdict="UNVERIFIED"         count={claimsUnverified} />
            <ConfidenceBadge verdict="UNSUPPORTED"        count={claimsUnsupported} />
          </div>
        </div>

        {/* Executive summary — editorial lede */}
        <div className="bg-blue-950/30 border border-blue-800/40 rounded-xl px-5 py-4 mb-5">
          <p className="text-[10px] font-bold text-blue-400 uppercase tracking-wider mb-2">
            Executive Summary
          </p>
          <p className="text-sm text-gray-200 leading-relaxed italic"
             style={{ fontFamily: "'Lora', Georgia, serif" }}>
            {executiveSummary}
          </p>
        </div>

        {/* Legend */}
        <div className="flex flex-wrap gap-3 px-4 py-3 bg-gray-900/40 rounded-xl border border-gray-800 text-xs text-gray-400 mb-2">
          <span className="font-medium text-gray-500">Legend:</span>
          <span className="flex items-center gap-1.5"><ConfidenceBadge verdict="VERIFIED" /> 3+ sources agree</span>
          <span className="flex items-center gap-1.5"><ConfidenceBadge verdict="PARTIALLY_VERIFIED" /> partial agreement</span>
          <span className="flex items-center gap-1.5"><ConfidenceBadge verdict="CONTESTED" /> conflicting evidence</span>
          <span className="flex items-center gap-1.5"><ConfidenceBadge verdict="UNVERIFIED" /> single source</span>
          <span className="flex items-center gap-1.5"><ConfidenceBadge verdict="UNSUPPORTED" /> no source found</span>
        </div>
      </div>

      {/* Article body — no individual section cards */}
      <article>
        {report.sections?.length > 0
          ? report.sections.map((s, i) => (
              <Section key={i} section={s} conflictMap={conflictMap} />
            ))
          : <p className="text-gray-500 text-sm py-8 text-center">No report sections generated.</p>
        }

        {/* Open Questions — italic pull-quotes */}
        {report.open_questions?.length > 0 && (
          <section className="mt-8 pt-6 border-t border-gray-800">
            <h3 className="text-xs font-mono font-bold text-gray-500 uppercase tracking-widest mb-5">
              Open Questions
            </h3>
            <div className="space-y-4">
              {report.open_questions.map((q, i) => (
                <p key={i}
                   className="text-[15px] text-gray-400 italic leading-relaxed pl-4 border-l-2 border-gray-700"
                   style={{ fontFamily: "'Lora', Georgia, serif" }}>
                  {q}
                </p>
              ))}
            </div>
          </section>
        )}

        {/* Claims list — new schema */}
        {report.claims?.length > 0 && (
          <section className="mt-8 pt-6 border-t border-gray-800">
            <ClaimsList claims={report.claims} />
          </section>
        )}
      </article>

      {/* Google Docs link */}
      {result.published?.doc_url && (
        <div className="flex items-center gap-3 px-5 py-3 mt-8 bg-gray-900/40 border border-gray-800 rounded-xl text-sm">
          <span className="text-gray-500">Published to</span>
          <a
            href={result.published.doc_url}
            target="_blank"
            rel="noreferrer"
            className="text-blue-400 hover:text-blue-300 underline underline-offset-2"
          >
            Google Docs ↗
          </a>
        </div>
      )}

      {/* Citation Graph — pass graphData prop from report if available */}
      <CitationGraph jobId={jobId} isComplete={true} graphData={report.citation_graph} />
    </div>
  )
}
