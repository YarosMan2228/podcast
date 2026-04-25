import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import ToneSelector from './ToneSelector.jsx'
import { showToast } from '../api/toast.js'

function parseTweets(textContent) {
  try {
    const parsed = JSON.parse(textContent)
    return Array.isArray(parsed.tweets) ? parsed.tweets : []
  } catch {
    return [textContent]
  }
}

function getPlainText(artifact) {
  if (artifact.type === 'TWITTER_THREAD') {
    return parseTweets(artifact.text_content)
      .map((t, i) => `${i + 1}/ ${t}`)
      .join('\n\n')
  }
  return artifact.text_content ?? ''
}

const MARKDOWN_TYPES = new Set(['LINKEDIN_POST', 'SHOW_NOTES', 'NEWSLETTER'])
const PREVIEW_LENGTH = 220

export default function TextArtifact({ artifact, onRegenerate }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const [tone, setTone] = useState(artifact.metadata?.tone ?? 'analytical')
  const [regenerating, setRegenerating] = useState(false)

  const isTwitter = artifact.type === 'TWITTER_THREAD'
  const text = artifact.text_content ?? ''
  const tweets = isTwitter ? parseTweets(text) : null
  const isLong = !isTwitter && text.length > PREVIEW_LENGTH
  const preview = isLong ? text.slice(0, PREVIEW_LENGTH) + '…' : text

  function handleCopy() {
    navigator.clipboard.writeText(getPlainText(artifact)).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      showToast('Copied to clipboard!')
    }).catch(() => {
      showToast('Copy failed — please select and copy manually', 'error')
    })
  }

  async function handleRegenerate() {
    setRegenerating(true)
    try {
      await onRegenerate?.(artifact, tone)
    } finally {
      setRegenerating(false)
    }
  }

  const typeLabel = artifact.type.replace(/_/g, ' ').toLowerCase()

  return (
    <div className="space-y-3">
      {/* Content */}
      {isTwitter ? (
        <div className="space-y-2 max-h-72 overflow-y-auto pr-1" role="list" aria-label="Twitter thread tweets">
          {tweets.map((tweet, i) => (
            <div
              key={i}
              role="listitem"
              className="p-3 bg-gray-50 rounded-lg border border-gray-100 text-sm text-gray-800 leading-relaxed"
            >
              <span className="text-xs text-gray-400 font-semibold mr-2" aria-hidden="true">{i + 1}/</span>
              {tweet}
            </div>
          ))}
        </div>
      ) : MARKDOWN_TYPES.has(artifact.type) ? (
        <div className={`overflow-hidden transition-all duration-300 ${expanded ? '' : 'max-h-28'}`}>
          <div className="prose prose-sm max-w-none text-gray-700">
            <ReactMarkdown>{expanded ? text : preview}</ReactMarkdown>
          </div>
        </div>
      ) : (
        <div className={`overflow-hidden text-sm text-gray-700 leading-relaxed transition-all duration-300 ${expanded ? '' : 'max-h-28'}`}>
          {expanded ? text : preview}
        </div>
      )}

      {isLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="text-xs text-indigo-600 hover:underline"
          aria-expanded={expanded}
          aria-label={expanded ? `Show less ${typeLabel}` : `Show full ${typeLabel}`}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}

      {/* Actions row */}
      <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-gray-100">
        <button
          onClick={handleCopy}
          aria-label={`Copy ${typeLabel} to clipboard`}
          className={`text-sm px-3 py-1.5 rounded-lg font-medium transition-colors ${
            copied
              ? 'bg-emerald-500 text-white'
              : 'border border-gray-300 text-gray-700 hover:bg-gray-50'
          }`}
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>

        <ToneSelector value={tone} onChange={setTone} />

        <button
          onClick={handleRegenerate}
          disabled={regenerating}
          aria-label={`Regenerate ${typeLabel} with ${tone} tone`}
          aria-busy={regenerating}
          className="text-sm px-3 py-1.5 border border-indigo-300 rounded-lg text-indigo-600 hover:bg-indigo-50 disabled:opacity-50 transition-colors"
        >
          {regenerating ? 'Regenerating…' : 'Regenerate'}
        </button>
      </div>
    </div>
  )
}
