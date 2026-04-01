import { useState, useRef, useEffect } from 'react'

export default function ChatPanel({ history, onSubmit, isResearching, error }) {
  const [input, setInput] = useState('')
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history])

  const handleSubmit = (e) => {
    e.preventDefault()
    const q = input.trim()
    if (!q || isResearching) return
    setInput('')
    onSubmit(q)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <span className="text-xl">🔬</span>
          <div>
            <h1 className="text-lg font-semibold text-white">Verity</h1>
            <p className="text-xs text-gray-500">Research that knows what it doesn't know</p>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto scrollbar-thin px-4 py-4 space-y-4">
        {history.length === 0 && (
          <div className="text-center text-gray-600 mt-16 px-4">
            <p className="text-4xl mb-4">🧬</p>
            <p className="text-sm font-medium text-gray-400 mb-2">Ask a complex research question</p>
            <p className="text-xs text-gray-600">Verity will audit your answer — labeling every claim as VERIFIED, CONTESTED, or UNVERIFIED</p>
          </div>
        )}

        {history.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${
              msg.role === 'user'
                ? 'bg-blue-600 text-white rounded-br-sm'
                : 'bg-gray-800 text-gray-200 rounded-bl-sm'
            }`}>
              <p className="leading-relaxed">{msg.content}</p>
              {msg.docUrl && (
                <a
                  href={msg.docUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 mt-2 text-xs text-blue-400 hover:text-blue-300 underline"
                >
                  📄 Open report
                </a>
              )}
            </div>
          </div>
        ))}

        {isResearching && (
          <div className="flex justify-start">
            <div className="bg-gray-800 rounded-2xl rounded-bl-sm px-4 py-3">
              <div className="flex gap-1.5 items-center">
                <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="bg-red-950 border border-red-800 rounded-xl px-4 py-3 text-sm text-red-400">
            ⚠️ {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 pb-4 pt-2 border-t border-gray-800">
        <form onSubmit={handleSubmit} className="flex gap-2">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) handleSubmit(e) }}
            placeholder="Ask a research question…"
            rows={2}
            disabled={isResearching}
            className="flex-1 bg-gray-800 border border-gray-700 rounded-xl px-4 py-3 text-sm text-gray-100 placeholder-gray-500 resize-none focus:outline-none focus:border-blue-500 disabled:opacity-50 transition-colors"
          />
          <button
            type="submit"
            disabled={!input.trim() || isResearching}
            className="self-end bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-xl px-4 py-3 text-sm font-medium transition-colors"
          >
            {isResearching ? '⏳' : '→'}
          </button>
        </form>
        <p className="text-xs text-gray-600 mt-2 text-center">
          Shift+Enter for new line · Enter to submit
        </p>
      </div>
    </div>
  )
}
