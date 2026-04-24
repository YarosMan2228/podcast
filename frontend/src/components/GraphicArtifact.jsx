import { useState } from 'react'

export default function GraphicArtifact({ artifact, onRegenerate }) {
  const { file_url, metadata = {} } = artifact
  const { quote_text, speaker } = metadata
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
        <img
          src={file_url}
          alt={quote_text ?? 'Quote graphic'}
          className="w-full rounded-lg aspect-square object-cover"
        />
      ) : (
        <div className="w-full rounded-lg bg-gray-100 flex items-center justify-center text-gray-400 text-sm aspect-square">
          No preview
        </div>
      )}

      {quote_text && (
        <p className="text-sm text-gray-700 italic leading-relaxed">"{quote_text}"</p>
      )}
      {speaker && <p className="text-xs text-gray-500">— {speaker}</p>}

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
