const TONES = [
  { value: 'analytical',   label: 'Analytical'   },
  { value: 'casual',       label: 'Casual'       },
  { value: 'punchy',       label: 'Punchy'       },
  { value: 'professional', label: 'Professional' },
]

export default function ToneSelector({ value, onChange }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="text-sm border border-gray-300 rounded-md px-2 py-1.5 text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
      aria-label="Select tone"
    >
      {TONES.map((t) => (
        <option key={t.value} value={t.value}>
          {t.label}
        </option>
      ))}
    </select>
  )
}
