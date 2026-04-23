import { useState, useEffect, useRef } from 'react'
import {
  job_state_processing,
  job_state_completed,
  MOCK_SSE_SEQUENCE,
} from '../api/mocks.js'

// Set to true to use real API (Day 4+). False = mock mode.
const USE_REAL_API = false

// ---------------------------------------------------------------------------
// Mock mode helpers
// ---------------------------------------------------------------------------

function buildArtifactMap(artifacts) {
  return Object.fromEntries(artifacts.map((a) => [a.id, a]))
}

export function applyEvent(job, artifactMap, eventType, payload) {
  switch (eventType) {
    case 'status_changed':
      return { job: { ...job, status: payload.status }, artifactMap }

    case 'artifact_ready': {
      const { artifact_id, type, index } = payload
      // Merge into the existing artifact entry from the initial snapshot
      const existing = artifactMap[artifact_id] ?? { id: artifact_id, type, index }
      // Find full data from completed snapshot (has file_url, text_content, etc.)
      const full = job_state_completed.artifacts.find((a) => a.id === artifact_id) ?? existing
      return {
        job: {
          ...job,
          progress: {
            ...job.progress,
            ready: (job.progress.ready ?? 0) + 1,
            processing: Math.max(0, (job.progress.processing ?? 0) - 1),
            queued: Math.max(0, (job.progress.queued ?? 0) - 1),
          },
        },
        artifactMap: { ...artifactMap, [artifact_id]: { ...full, status: 'READY' } },
      }
    }

    case 'artifact_failed': {
      const { artifact_id, error } = payload
      const existing = artifactMap[artifact_id] ?? { id: artifact_id }
      return {
        job: {
          ...job,
          progress: {
            ...job.progress,
            failed: (job.progress.failed ?? 0) + 1,
            processing: Math.max(0, (job.progress.processing ?? 0) - 1),
            queued: Math.max(0, (job.progress.queued ?? 0) - 1),
          },
        },
        artifactMap: { ...artifactMap, [artifact_id]: { ...existing, status: 'FAILED', error } },
      }
    }

    case 'completed':
      return {
        job: { ...job, status: 'COMPLETED', package_url: payload.package_url },
        artifactMap,
      }

    default:
      return { job, artifactMap }
  }
}

function useMockJob() {
  const [job, setJob] = useState(job_state_processing)
  const [artifactMap, setArtifactMap] = useState(() =>
    buildArtifactMap(job_state_processing.artifacts),
  )
  const timersRef = useRef([])

  useEffect(() => {
    let currentJob = job_state_processing
    let currentMap = buildArtifactMap(job_state_processing.artifacts)

    for (const [delayMs, eventType, payload] of MOCK_SSE_SEQUENCE) {
      const id = setTimeout(() => {
        const next = applyEvent(currentJob, currentMap, eventType, payload)
        currentJob = next.job
        currentMap = next.artifactMap
        setJob({ ...currentJob })
        setArtifactMap({ ...currentMap })
      }, delayMs)
      timersRef.current.push(id)
    }

    return () => {
      timersRef.current.forEach(clearTimeout)
      timersRef.current = []
    }
  }, [])

  const artifacts = Object.values(artifactMap)
  return { job, artifacts, isConnected: true }
}

// ---------------------------------------------------------------------------
// Real API mode (Day 4+)
// ---------------------------------------------------------------------------

function useRealJob(jobId) {
  const [job, setJob] = useState(null)
  const [artifactMap, setArtifactMap] = useState({})
  const [isConnected, setIsConnected] = useState(false)
  const esRef = useRef(null)
  const pollRef = useRef(null)

  useEffect(() => {
    if (!jobId) return

    async function fetchJob() {
      try {
        const res = await fetch(`/api/jobs/${jobId}`)
        if (!res.ok) return
        const data = await res.json()
        setJob(data)
        setArtifactMap(buildArtifactMap(data.artifacts ?? []))
      } catch {
        // silent — SSE is primary; polling is fallback
      }
    }

    fetchJob()

    const es = new EventSource(`/api/jobs/${jobId}/events`)
    esRef.current = es

    es.onopen = () => {
      setIsConnected(true)
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }

    es.onerror = () => {
      setIsConnected(false)
      if (!pollRef.current) {
        pollRef.current = setInterval(fetchJob, 5000)
      }
    }

    function handleEvent(eventType, payload) {
      setJob((prev) => {
        if (!prev) return prev
        setArtifactMap((prevMap) => {
          const { job: nextJob, artifactMap: nextMap } = applyEvent(prev, prevMap, eventType, payload)
          setJob(nextJob)
          return nextMap
        })
        return prev
      })
    }

    es.addEventListener('status_changed',  (e) => handleEvent('status_changed',  JSON.parse(e.data)))
    es.addEventListener('artifact_ready',  (e) => handleEvent('artifact_ready',  JSON.parse(e.data)))
    es.addEventListener('artifact_failed', (e) => handleEvent('artifact_failed', JSON.parse(e.data)))
    es.addEventListener('completed',       (e) => handleEvent('completed',       JSON.parse(e.data)))

    return () => {
      es.close()
      esRef.current = null
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [jobId])

  const artifacts = Object.values(artifactMap)
  return { job, artifacts, isConnected }
}

// ---------------------------------------------------------------------------
// Public hook — switches between mock and real based on USE_REAL_API flag
// ---------------------------------------------------------------------------

export default function useJob(jobId) {
  if (USE_REAL_API) {
    // Rules-of-hooks: both branches always call the same hook internally.
    // The flag is a module-level constant, so the call order never changes at runtime.
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useRealJob(jobId)
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useMockJob()
}
