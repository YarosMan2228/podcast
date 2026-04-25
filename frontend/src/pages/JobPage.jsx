import { useParams } from 'react-router-dom'
import useJob from '../hooks/useJob.js'
import JobProgressBar from '../components/JobProgressBar.jsx'
import ArtifactCard from '../components/ArtifactCard.jsx'
import { regenerateArtifact } from '../api/client.js'
import { showToast } from '../api/toast.js'

const SECTION_ORDER = [
  'VIDEO_CLIP',
  'LINKEDIN_POST',
  'TWITTER_THREAD',
  'SHOW_NOTES',
  'NEWSLETTER',
  'YOUTUBE_DESCRIPTION',
  'QUOTE_GRAPHIC',
]

const SECTION_LABELS = {
  VIDEO_CLIP:          'Video Clips',
  LINKEDIN_POST:       'LinkedIn',
  TWITTER_THREAD:      'Twitter Thread',
  SHOW_NOTES:          'Show Notes',
  NEWSLETTER:          'Newsletter',
  YOUTUBE_DESCRIPTION: 'YouTube Description',
  QUOTE_GRAPHIC:       'Quote Graphics',
}

function groupByType(artifacts) {
  const groups = {}
  for (const art of artifacts) {
    if (!groups[art.type]) groups[art.type] = []
    groups[art.type].push(art)
  }
  return groups
}

export default function JobPage() {
  const { jobId } = useParams()
  const { job, artifacts, isConnected, refetch } = useJob(jobId)

  async function handleRegenerate(artifact, tone) {
    try {
      await regenerateArtifact(artifact.id, tone ?? null)
      refetch()
      showToast('Regenerating…')
    } catch (err) {
      showToast(err.message ?? 'Regenerate failed', 'error')
    }
  }

  if (!job) {
    return (
      <div className="min-h-screen flex items-center justify-center" aria-label="Loading job">
        <div
          className="w-8 h-8 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin"
          aria-label="Loading"
          role="status"
        />
      </div>
    )
  }

  if (job.status === 'FAILED') {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center gap-4 px-4 text-center">
        <p className="text-red-600 font-semibold text-lg" role="alert">Processing failed</p>
        <p className="text-gray-500 text-sm max-w-md">{job.error ?? 'An unexpected error occurred.'}</p>
        <a href="/" className="text-indigo-600 hover:underline text-sm">
          Try again with a different file
        </a>
      </main>
    )
  }

  const isCompleted = job.status === 'COMPLETED'
  const groups = groupByType(artifacts)
  const { progress } = job

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-100 sticky top-0 z-10 px-4 py-3 shadow-sm">
        <div className="max-w-5xl mx-auto flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0 flex-1">
            {job.analysis?.episode_title ? (
              <h1 className="text-base sm:text-lg font-bold text-gray-900 truncate">
                {job.analysis.episode_title}
              </h1>
            ) : (
              <h1 className="text-base sm:text-lg font-bold text-gray-400">Processing your episode…</h1>
            )}
            {job.analysis?.hook && (
              <p className="text-xs text-gray-500 mt-0.5 line-clamp-1">{job.analysis.hook}</p>
            )}
          </div>

          {isCompleted && job.package_url && (
            <a
              href={job.package_url}
              download
              aria-label="Download all artifacts as ZIP"
              className="shrink-0 bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700 transition-colors"
            >
              Download All (ZIP)
            </a>
          )}
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6 space-y-8">
        {/* Progress section — visible while processing */}
        {!isCompleted && (
          <section aria-label="Processing progress" className="flex flex-col items-center gap-3 py-2">
            <JobProgressBar status={job.status} />

            {progress && (
              <p className="text-sm text-gray-500" role="status" aria-live="polite">
                {progress.ready}/{progress.total_artifacts} artifacts ready
                {progress.failed > 0 && (
                  <span className="text-red-500 ml-2">· {progress.failed} failed</span>
                )}
              </p>
            )}

            {!isConnected && (
              <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 px-3 py-1.5 rounded-lg" role="status" aria-live="assertive">
                Connection lost — polling for updates…
              </p>
            )}
          </section>
        )}

        {/* Artifact sections */}
        {SECTION_ORDER.map((type) => {
          const group = groups[type]
          if (!group || group.length === 0) return null

          const cols =
            type === 'VIDEO_CLIP' || type === 'QUOTE_GRAPHIC'
              ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3'
              : 'grid-cols-1 lg:grid-cols-2'

          return (
            <section key={type} aria-label={SECTION_LABELS[type]}>
              <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">
                {SECTION_LABELS[type]}
              </h2>
              <div className={`grid ${cols} gap-4`}>
                {group
                  .slice()
                  .sort((a, b) => a.index - b.index)
                  .map((art) => (
                    <ArtifactCard
                      key={art.id}
                      artifact={art}
                      onRegenerate={handleRegenerate}
                    />
                  ))}
              </div>
            </section>
          )
        })}

        {/* Empty state — no artifacts yet (pre-fan-out) */}
        {!isCompleted && artifacts.length === 0 && (
          <p className="text-center text-gray-400 text-sm py-12" role="status">
            Artifacts will appear here as they are generated…
          </p>
        )}
      </main>
    </div>
  )
}
