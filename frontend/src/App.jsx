import { useState, useCallback } from 'react'
import ChatPanel from './components/ChatPanel.jsx'
import ResearchFeed from './components/ResearchFeed.jsx'

export default function App() {
  const [jobId, setJobId] = useState(null)
  const [isResearching, setIsResearching] = useState(false)
  const [agentEvents, setAgentEvents] = useState([])
  const [result, setResult] = useState(null)       // { report, conflict_reports, published }
  const [history, setHistory] = useState([])
  const [error, setError] = useState(null)

  const handleSubmit = useCallback(async (query) => {
    setError(null)
    setResult(null)
    setAgentEvents([])
    setIsResearching(true)
    setJobId(null)

    setHistory(prev => [...prev, { role: 'user', content: query }])

    try {
      const res = await fetch('/research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })
      if (!res.ok) throw new Error(`Server error: ${res.status}`)
      const { job_id } = await res.json()
      setJobId(job_id)

      const source = new EventSource(`/stream/${job_id}`)

      source.onmessage = (e) => {
        const data = JSON.parse(e.data)

        if (data.heartbeat) return

        if (data.status === 'done' && data.job_id) {
          source.close()
          setIsResearching(false)
          fetch(`/result/${job_id}`)
            .then(r => r.json())
            .then(res => {
              setResult(res)
              const summary = res.report?.summary ?? 'Research complete.'
              const counts = res.report
                ? `${res.report.verified_count} verified · ${res.report.contested_count} contested · ${res.report.unverified_count} unverified`
                : ''
              setHistory(prev => [...prev, {
                role: 'assistant',
                content: `${summary}${counts ? '\n\n' + counts : ''}`,
              }])
            })
            .catch(err => setError(err.message))
          return
        }

        if (data.status === 'error') {
          source.close()
          setIsResearching(false)
          setError(data.message)
          return
        }

        // Update agent event cards
        setAgentEvents(prev => {
          const idx = prev.findIndex(e => e.agent === data.agent && e.status !== 'done')
          if (idx >= 0) {
            const updated = [...prev]
            updated[idx] = data
            return updated
          }
          return [...prev, data]
        })
      }

      source.onerror = () => {
        source.close()
        setIsResearching(false)
        if (!result) setError('Connection lost. Please try again.')
      }
    } catch (err) {
      setIsResearching(false)
      setError(err.message)
    }
  }, [result])

  return (
    <div className="flex h-screen overflow-hidden bg-gray-950">
      <div className="w-[38%] min-w-[300px] border-r border-gray-800 flex flex-col">
        <ChatPanel
          history={history}
          onSubmit={handleSubmit}
          isResearching={isResearching}
          error={error}
        />
      </div>
      <div className="flex-1 flex flex-col overflow-hidden">
        <ResearchFeed
          agentEvents={agentEvents}
          result={result}
          isResearching={isResearching}
          jobId={jobId}
        />
      </div>
    </div>
  )
}
