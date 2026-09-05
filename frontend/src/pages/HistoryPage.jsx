import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { listJobs, deleteJob } from '../api/client.js'
import { showToast } from '../api/toast.js'

const STATUS_STYLES = {
  COMPLETED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  FAILED:    'bg-red-50 text-red-700 border-red-200',
  PENDING:   'bg-gray-100 text-gray-600 border-gray-200',
}
const IN_PROGRESS = 'bg-indigo-50 text-indigo-700 border-indigo-200'

function statusClass(status) {
  return STATUS_STYLES[status] ?? IN_PROGRESS
}

function formatDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function formatDuration(sec) {
  if (sec == null) return null
  const m = Math.round(sec / 60)
  return m < 1 ? '<1 min' : `${m} min`
}

function jobTitle(job) {
  return job.episode_title || job.original_filename || job.source_url || 'Untitled episode'
}

export function JobRow({ job, onDelete }) {
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const isTerminal = job.status === 'COMPLETED' || job.status === 'FAILED'

  async function handleDelete() {
    setDeleting(true)
    try {
      await onDelete(job)
    } finally {
      setDeleting(false)
      setConfirming(false)
    }
  }

  return (
    <li className="bg-white border border-gray-200 rounded-2xl p-4 flex flex-col sm:flex-row sm:items-center gap-3">
      <div className="min-w-0 flex-1">
        <Link
          to={`/jobs/${job.job_id}`}
          className="font-semibold text-gray-900 hover:text-indigo-600 truncate block"
        >
          {jobTitle(job)}
        </Link>
        {job.hook && <p className="text-xs text-gray-500 line-clamp-1 mt-0.5">{job.hook}</p>}
        <p className="text-xs text-gray-400 mt-1 flex flex-wrap gap-x-3">
          <span>{formatDate(job.created_at)}</span>
          {formatDuration(job.duration_sec) && <span>{formatDuration(job.duration_sec)}</span>}
          {job.progress?.total_artifacts > 0 && (
            <span>
              {job.progress.ready}/{job.progress.total_artifacts} ready
              {job.progress.failed > 0 && <span className="text-red-500"> · {job.progress.failed} failed</span>}
            </span>
          )}
        </p>
        {job.status === 'FAILED' && job.error && (
          <p className="text-xs text-red-500 mt-1 line-clamp-1">{job.error}</p>
        )}
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full border ${statusClass(job.status)}`}>
          {job.status}
        </span>
        {job.status === 'COMPLETED' && job.has_package && (
          <a
            href={`/api/jobs/${job.job_id}/download`}
            download
            aria-label={`Download ZIP for ${jobTitle(job)}`}
            className="text-xs px-3 py-1.5 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
          >
            ZIP
          </a>
        )}
        {!isTerminal && (
          <Link
            to={`/jobs/${job.job_id}`}
            className="text-xs px-3 py-1.5 border border-indigo-300 rounded-lg text-indigo-600 hover:bg-indigo-50"
          >
            Watch
          </Link>
        )}
        {confirming ? (
          <span className="flex items-center gap-1">
            <button
              onClick={handleDelete}
              disabled={deleting}
              aria-label={`Confirm delete ${jobTitle(job)}`}
              className="text-xs px-3 py-1.5 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50"
            >
              {deleting ? 'Deleting…' : 'Yes, delete'}
            </button>
            <button
              onClick={() => setConfirming(false)}
              disabled={deleting}
              className="text-xs px-2 py-1.5 text-gray-500 hover:text-gray-700"
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            onClick={() => setConfirming(true)}
            aria-label={`Delete ${jobTitle(job)}`}
            className="text-xs px-3 py-1.5 border border-red-200 rounded-lg text-red-600 hover:bg-red-50"
          >
            Delete
          </button>
        )}
      </div>
    </li>
  )
}

export default function HistoryPage() {
  const [jobs, setJobs] = useState(null)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    listJobs(100)
      .then((data) => {
        if (cancelled) return
        setJobs(data.jobs ?? [])
        setError('')
      })
      .catch((err) => {
        if (cancelled) return
        setError(err.message ?? 'Could not load history')
        setJobs([])
      })
    return () => { cancelled = true }
  }, [reloadKey])

  const load = useCallback(() => setReloadKey((k) => k + 1), [])

  async function handleDelete(job) {
    try {
      await deleteJob(job.job_id)
      setJobs((prev) => (prev ?? []).filter((j) => j.job_id !== job.job_id))
      showToast('Episode deleted')
    } catch (err) {
      showToast(err.message ?? 'Delete failed', 'error')
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-100 px-4 py-3">
        <div className="max-w-4xl mx-auto flex items-center justify-between gap-3">
          <h1 className="text-lg font-bold text-gray-900">Your episodes</h1>
          <Link
            to="/"
            className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700"
          >
            + New episode
          </Link>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-6">
        {jobs === null && (
          <p className="text-sm text-gray-400 py-12 text-center" role="status">Loading…</p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-4">
            {error}
            <button onClick={load} className="ml-3 underline">Retry</button>
          </p>
        )}
        {jobs && jobs.length === 0 && !error && (
          <p className="text-sm text-gray-400 py-12 text-center" role="status">
            No episodes yet. <Link to="/" className="text-indigo-600 underline">Upload one</Link>.
          </p>
        )}
        {jobs && jobs.length > 0 && (
          <ul className="space-y-3 list-none" aria-label="Episode history">
            {jobs.map((job) => (
              <JobRow key={job.job_id} job={job} onDelete={handleDelete} />
            ))}
          </ul>
        )}
      </main>
    </div>
  )
}
