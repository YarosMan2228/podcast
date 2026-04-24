const PHASES = [
  { key: 'INGESTING',    label: 'Uploading'    },
  { key: 'TRANSCRIBING', label: 'Transcribing' },
  { key: 'ANALYZING',    label: 'Analyzing'    },
  { key: 'GENERATING',   label: 'Generating'   },
  { key: 'PACKAGING',    label: 'Packaging'    },
  { key: 'COMPLETED',    label: 'Complete'     },
]

function phaseIndex(status) {
  const idx = PHASES.findIndex((p) => p.key === status)
  return idx === -1 ? 0 : idx
}

export default function JobProgressBar({ status }) {
  const current = phaseIndex(status)
  const pct = PHASES.length > 1 ? (current / (PHASES.length - 1)) * 100 : 0

  return (
    <div className="w-full max-w-2xl" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="flex justify-between mb-2">
        {PHASES.map((phase, i) => (
          <span
            key={phase.key}
            className={`text-xs ${
              i < current
                ? 'text-indigo-500 font-medium'
                : i === current
                ? 'text-indigo-700 font-semibold'
                : 'text-gray-400'
            }`}
          >
            {phase.label}
          </span>
        ))}
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2">
        <div
          className="bg-indigo-600 h-2 rounded-full transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
