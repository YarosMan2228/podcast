import { useState } from 'react'

export default function VideoArtifact({ artifact, onRegenerate }) {
  const { file_url, metadata = {}, version } = artifact
  const { virality_score, duration_sec, hook_text } = metadata
  const [regenerating, setRegenerating] = useState(false)

  async function handleRegenerate() {
    setRegenerating(true)
    try {
      await onRegenerate?.(artifact)
    } finally {
      setRegenerating(false)
    }
  }

  return (
    <div className="space-y-3">
      {file_url ? (
        <video
          className="w-full rounded-lg bg-black"
          style={{ aspectRatio: '9/16', objectFit: 'cover' }}
          src={file_url}
          controls
          playsInline
          preload="metadata"
        />
      ) : (
        <div className="w-full rounded-lg bg-gray-100 flex items-center justify-center text-gray-400 text-sm" style={{ aspectRatio: '9/16' }}>
          No preview
        </div>
      )}

      {hook_text && (
        <p className="text-sm font-medium text-gray-800 leading-snug">{hook_text}</p>
      )}

      <div className="flex items-center gap-3 text-xs text-gray-400">
        {virality_score != null && (
          <span className="font-medium text-indigo-600">Virality {virality_score}/10</span>
        )}
        {duration_sec != null && <span>{Math.round(duration_sec)}s</span>}
        {version > 1 && <span>v{version}</span>}
      </div>

      <div className="flex gap-2">
        {file_url && (
          <a
            href={file_url}
            download
            className="flex-1 text-center text-sm px-3 py-1.5 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
          >
            Download
          </a>
        )}
        <button
          onClick={handleRegenerate}
          disabled={regenerating}
          className="flex-1 text-sm px-3 py-1.5 border border-indigo-300 rounded-lg text-indigo-600 hover:bg-indigo-50 disabled:opacity-50 transition-colors"
        >
          {regenerating ? 'Regenerating…' : 'Regenerate'}
        </button>
      </div>
    </div>
  )
}
