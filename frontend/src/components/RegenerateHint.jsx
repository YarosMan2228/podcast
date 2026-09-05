/**
 * Small optional text input shown next to "Regenerate" (Pro).
 * The hint is sent to the API and steers the next version:
 * text → appended to the Claude prompt; video → picks the clip candidate
 * whose hook/reason best matches the words.
 */
export default function RegenerateHint({ value, onChange, placeholder = 'What to change? (optional)' }) {
  return (
    <input
      type="text"
      value={value}
      maxLength={300}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      aria-label="Regenerate hint"
      className="flex-1 min-w-[140px] text-sm border border-gray-200 rounded-lg px-3 py-1.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
    />
  )
}
