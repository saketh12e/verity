import { useEffect, useRef } from 'react'
import AgentCard from './AgentCard.jsx'
import ReportView from './ReportView.jsx'

// All possible agent slots — crawlers shown dynamically
const STATIC_AGENTS = [
  { key: 'decomposer',      icon: '🧩', label: 'Decomposer',         desc: 'Splitting query into research angles' },
  { key: 'dedup_agent',     icon: '🗃️', label: 'Dedup',              desc: 'Removing duplicate findings' },
  { key: 'conflict_agent',  icon: '⚖️', label: 'Conflict Detector',  desc: 'Cross-checking for disagreements' },
  { key: 'devil_advocate',  icon: '😈', label: "Devil's Advocate",   desc: 'Challenging high-confidence claims' },
  { key: 'synthesis_agent', icon: '📝', label: 'Synthesis',           desc: 'Writing research report' },
]

export default function ResearchFeed({ agentEvents, result, isResearching, jobId }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [agentEvents, result])

  // Build a map keyed by agent name; crawlers accumulate
  const eventMap = {}
  const crawlerEvents = []

  for (const evt of agentEvents) {
    if (evt.agent === 'crawler_agent') {
      crawlerEvents.push(evt)
    } else {
      eventMap[evt.agent] = evt
    }
  }

  const isEmpty = !isResearching && agentEvents.length === 0 && !result
  const showFeed = isResearching || (agentEvents.length > 0 && !result)

  return (
    <div className="flex flex-col h-full bg-gray-950">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between shrink-0">
        <div>
          <h2 className="text-sm font-semibold text-gray-200">
            {result ? 'Research Report' : isResearching ? 'Live Agent Feed' : 'Research Feed'}
          </h2>
          {jobId && (
            <p className="text-[10px] text-gray-600 font-mono mt-0.5">job {jobId.slice(0, 8)}</p>
          )}
        </div>
        {isResearching && (
          <div className="flex items-center gap-2 text-xs text-blue-400">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
            </span>
            Researching
          </div>
        )}
        {result && (
          <div className="flex items-center gap-2 text-xs text-green-400">
            <span className="w-2 h-2 rounded-full bg-green-400" />
            Complete
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin px-6 py-5">
        {isEmpty && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <p className="text-6xl mb-5">🔬</p>
            <p className="text-base font-semibold text-gray-400 mb-2">Ask a research question</p>
            <p className="text-sm text-gray-600 max-w-sm">
              Watch agents decompose your question, search the web, detect contradictions, and produce an auditable report with confidence labels on every claim.
            </p>
            <div className="mt-6 grid grid-cols-3 gap-3 text-center max-w-sm w-full">
              {[
                { icon: '✓', label: 'VERIFIED', sub: '3+ sources agree', color: 'text-green-400' },
                { icon: '⚡', label: 'CONTESTED', sub: 'Sources disagree', color: 'text-yellow-400' },
                { icon: '?', label: 'UNVERIFIED', sub: 'Single source', color: 'text-gray-500' },
              ].map(b => (
                <div key={b.label} className="bg-gray-900 rounded-xl p-3 border border-gray-800">
                  <p className={`text-lg font-bold ${b.color}`}>{b.icon}</p>
                  <p className={`text-xs font-mono font-semibold ${b.color}`}>{b.label}</p>
                  <p className="text-[10px] text-gray-600 mt-0.5">{b.sub}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Agent feed */}
        {showFeed && (
          <div className="space-y-2 mb-6">
            {/* Decomposer */}
            <AgentCard
              icon={eventMap['decomposer']?.icon ?? '🧩'}
              label={eventMap['decomposer']?.label ?? 'Decomposer'}
              status={eventMap['decomposer']?.status ?? 'waiting'}
              message={eventMap['decomposer']?.message}
            />

            {/* Crawlers — dynamic based on actual events */}
            {crawlerEvents.length > 0 ? (
              crawlerEvents.map((evt, i) => (
                <AgentCard
                  key={`crawler-${i}`}
                  icon={evt.icon ?? '🔍'}
                  label={evt.label ?? `Crawler ${i + 1}`}
                  status={evt.status}
                  message={evt.message}
                />
              ))
            ) : (
              // Placeholder crawlers while decomposer runs
              eventMap['decomposer']?.status === 'done' || isResearching ? (
                [1, 2, 3, 4].map(n => (
                  <AgentCard key={`crawler-placeholder-${n}`} icon="🔍" label={`Crawler ${n}`} status="waiting" />
                ))
              ) : null
            )}

            {/* Remaining agents */}
            {['dedup_agent', 'conflict_agent', 'devil_advocate', 'synthesis_agent'].map(key => {
              const meta = STATIC_AGENTS.find(a => a.key === key)
              const evt = eventMap[key]
              return (
                <AgentCard
                  key={key}
                  icon={evt?.icon ?? meta?.icon}
                  label={evt?.label ?? meta?.label}
                  status={evt?.status ?? 'waiting'}
                  message={evt?.message ?? (isResearching ? meta?.desc : undefined)}
                />
              )
            })}
          </div>
        )}

        {/* Report */}
        {result && <ReportView result={result} jobId={jobId} />}

        <div ref={bottomRef} />
      </div>
    </div>
  )
}
