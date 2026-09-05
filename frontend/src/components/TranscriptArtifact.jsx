import { useState } from 'react'
import { showToast } from '../api/toast.js'

const PREVIEW_CHARS = 600

const LANGUAGE_LABELS = {
  en: 'English', ru: 'Русский', uk: 'Українська', de: 'Deutsch', fr: 'Français',
  es: 'Español', it: 'Italiano', pt: 'Português', pl: 'Polski', tr: 'Türkçe',
}

function languageLabel(code) {
  if (!code) return null
  return LANGUAGE_LABELS[code] ?? code.toUpperCase()
}

export default function TranscriptArtifact({ artifact }) {
  const { text_content, metadata = {}, files = {} } = artifact
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  const text = text_content ?? ''
  const isLong = text.length > PREVIEW_CHARS
  const shown = expanded || !isLong ? text : text.slice(0, PREVIEW_CHARS) + '…'
  const lang = languageLabel(metadata.language)

  function handleCopy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      showToast('Transcript copied!')
    }).catch(() => {
      showToast('Copy failed — please select and copy manually', 'error')
    })
  }

  function handleDownloadTxt() {
    // Client-side blob: the .txt lives only in text_content (the ZIP has it
    // too, but users often want just this one file).
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'transcript.txt'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500" aria-label="Transcript metadata">
        {lang && <span className="px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 font-medium">{lang}</span>}
        {metadata.word_count != null && <span>{metadata.word_count.toLocaleString()} words</span>}
        {metadata.duration_sec != null && <span>{Math.round(metadata.duration_sec / 60)} min</span>}
      </div>

      <pre
        className={`whitespace-pre-wrap font-sans text-sm text-gray-700 leading-relaxed overflow-hidden ${expanded ? '' : 'max-h-56'}`}
      >
        {shown}
      </pre>

      {isLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="text-xs text-indigo-600 hover:underline"
          aria-expanded={expanded}
          aria-label={expanded ? 'Show less transcript' : 'Show full transcript'}
        >
          {expanded ? 'Show less' : 'Show full transcript'}
        </button>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-gray-100">
        <button
          onClick={handleCopy}
          aria-label="Copy transcript to clipboard"
          className={`text-sm px-3 py-1.5 rounded-lg font-medium transition-colors ${
            copied ? 'bg-emerald-500 text-white' : 'border border-gray-300 text-gray-700 hover:bg-gray-50'
          }`}
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
        <button
          onClick={handleDownloadTxt}
          aria-label="Download transcript as TXT"
          className="text-sm px-3 py-1.5 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
        >
          TXT
        </button>
        {files.srt && (
          <a
            href={files.srt}
            download="transcript.srt"
            aria-label="Download SRT captions"
            className="text-sm px-3 py-1.5 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
          >
            SRT
          </a>
        )}
        {files.vtt && (
          <a
            href={files.vtt}
            download="transcript.vtt"
            aria-label="Download VTT captions"
            className="text-sm px-3 py-1.5 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
          >
            VTT
          </a>
        )}
      </div>
    </div>
  )
}
