import { useState } from 'react'

function isValidHttpUrl(val) {
  try {
    const u = new URL(val)
    return u.protocol === 'http:' || u.protocol === 'https:'
  } catch {
    return false
  }
}

export default function UrlInput({ onSubmit }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    const val = url.trim()
    if (!val) return
    if (!isValidHttpUrl(val)) {
      setError('Please enter a valid URL (YouTube, Spotify, SoundCloud)')
      return
    }
    setError('')
    setSubmitting(true)
    try {
      await onSubmit?.(val)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="w-full max-w-xl" noValidate>
      <div className="flex gap-2">
        <input
          type="url"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value)
            if (error) setError('')
          }}
          placeholder="https://youtube.com/watch?v=..."
          className={`flex-1 border rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white ${
            error ? 'border-red-400' : 'border-gray-300'
          }`}
          aria-label="Podcast URL"
          disabled={submitting}
        />
        <button
          type="submit"
          disabled={submitting || !url.trim()}
          className="px-5 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {submitting ? '…' : 'Go'}
        </button>
      </div>
      {error && <p className="mt-1.5 text-xs text-red-500" role="alert">{error}</p>}
    </form>
  )
}
