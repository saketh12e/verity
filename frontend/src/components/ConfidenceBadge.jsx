const CONFIG = {
  VERIFIED:           { bg: 'bg-green-900/60 text-green-300 border-green-700',    icon: '✓' },
  PARTIALLY_VERIFIED: { bg: 'bg-yellow-900/60 text-yellow-300 border-yellow-700', icon: '~' },
  UNVERIFIED:         { bg: 'bg-orange-900/60 text-orange-300 border-orange-600', icon: '?' },
  UNSUPPORTED:        { bg: 'bg-red-900/60 text-red-300 border-red-700',           icon: '✗' },
  // Keep old CONTESTED as alias for PARTIALLY_VERIFIED
  CONTESTED:          { bg: 'bg-yellow-900/60 text-yellow-300 border-yellow-700', icon: '⚡' },
}

export function ConfidenceBadge({ verdict, count }) {
  const { bg, icon } = CONFIG[verdict] ?? CONFIG.UNVERIFIED
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border font-mono ${bg}`}>
      {icon} {verdict}{count !== undefined ? ` · ${count}` : ''}
    </span>
  )
}
