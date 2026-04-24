import VideoArtifact from './VideoArtifact.jsx'
import TextArtifact from './TextArtifact.jsx'
import GraphicArtifact from './GraphicArtifact.jsx'

const TYPE_LABELS = {
  VIDEO_CLIP:          'Video Clip',
  LINKEDIN_POST:       'LinkedIn Post',
  TWITTER_THREAD:      'Twitter Thread',
  SHOW_NOTES:          'Show Notes',
  NEWSLETTER:          'Newsletter',
  YOUTUBE_DESCRIPTION: 'YouTube Description',
  QUOTE_GRAPHIC:       'Quote Graphic',
}

const TEXT_TYPES = new Set([
  'LINKEDIN_POST',
  'TWITTER_THREAD',
  'SHOW_NOTES',
  'NEWSLETTER',
  'YOUTUBE_DESCRIPTION',
])

function Spinner({ className = '' }) {
  return (
    <div
      className={`w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin ${className}`}
      aria-hidden="true"
    />
  )
}

export default function ArtifactCard({ artifact, onRegenerate }) {
  const label = TYPE_LABELS[artifact.type] ?? artifact.type

  if (artifact.status === 'QUEUED') {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-4 animate-pulse">
        <div className="h-3 bg-gray-200 rounded w-1/3 mb-3" />
        <div className="h-3 bg-gray-100 rounded w-2/3 mb-2" />
        <div className="h-3 bg-gray-100 rounded w-1/2" />
        <p className="mt-4 text-xs text-gray-400">{label} · Queued</p>
      </div>
    )
  }

  if (artifact.status === 'PROCESSING') {
    return (
      <div className="rounded-2xl border border-indigo-100 bg-white p-4">
        <div className="flex items-center gap-2 mb-2">
          <Spinner className="text-indigo-500" />
          <span className="text-sm font-medium text-indigo-700">{label}</span>
        </div>
        <p className="text-xs text-gray-400">Processing…</p>
      </div>
    )
  }

  if (artifact.status === 'FAILED') {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 p-4">
        <p className="text-sm font-semibold text-red-700">{label}</p>
        <p className="mt-1 text-xs text-red-500 leading-relaxed">
          {artifact.error ?? 'Generation failed'}
        </p>
        <button
          onClick={() => onRegenerate?.(artifact)}
          className="mt-3 text-sm px-3 py-1.5 border border-red-300 text-red-600 rounded-lg hover:bg-red-100 transition-colors"
          aria-label={`Retry ${label}`}
        >
          Retry
        </button>
      </div>
    )
  }

  // READY
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">{label}</p>

      {artifact.type === 'VIDEO_CLIP' && (
        <VideoArtifact artifact={artifact} onRegenerate={onRegenerate} />
      )}
      {TEXT_TYPES.has(artifact.type) && (
        <TextArtifact artifact={artifact} onRegenerate={onRegenerate} />
      )}
      {artifact.type === 'QUOTE_GRAPHIC' && (
        <GraphicArtifact artifact={artifact} onRegenerate={onRegenerate} />
      )}
    </div>
  )
}
