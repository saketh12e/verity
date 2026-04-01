const STATUS_STYLES = {
  waiting: 'text-gray-500',
  running: 'text-blue-400',
  done:    'text-green-400',
  error:   'text-red-400',
}

const DOT_STYLES = {
  waiting: 'bg-gray-600',
  running: 'bg-blue-400 animate-pulse',
  done:    'bg-green-400',
  error:   'bg-red-400',
}

export default function AgentCard({ icon, label, status, message }) {
  const s = status || 'waiting'
  return (
    <div className={`flex items-start gap-3 rounded-xl border px-4 py-3 transition-all duration-300 ${
      s === 'running' ? 'border-blue-700 bg-blue-950/30' :
      s === 'done'    ? 'border-green-900 bg-green-950/20' :
      s === 'error'   ? 'border-red-900 bg-red-950/20' :
      'border-gray-800 bg-gray-900/40'
    }`}>
      <span className="text-xl leading-none mt-0.5">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={`text-sm font-medium ${STATUS_STYLES[s]}`}>{label}</span>
          <span className={`w-2 h-2 rounded-full shrink-0 ${DOT_STYLES[s]}`} />
        </div>
        {message && (
          <p className="text-xs text-gray-500 mt-0.5 truncate">{message}</p>
        )}
      </div>
    </div>
  )
}
