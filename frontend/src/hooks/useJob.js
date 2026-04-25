import { useState, useReducer, useEffect, useRef, useCallback } from 'react'
import { job_state_processing, MOCK_SSE_SEQUENCE } from '../api/mocks.js'

const USE_REAL_API = true

// ---------------------------------------------------------------------------
// Shared helpers
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
      const existing = artifactMap[artifact_id] ?? { id: artifact_id, type, index }
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
        artifactMap: { ...artifactMap, [artifact_id]: { ...existing, status: 'READY' } },
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

// ---------------------------------------------------------------------------
// Mock mode
// ---------------------------------------------------------------------------

function mockReducer(state, action) {
  if (action.type === 'EVENT') {
    return applyEvent(state.job, state.artifactMap, action.eventType, action.payload)
  }
  return state
}

function useMockJob() {
  const [state, dispatch] = useReducer(mockReducer, {
    job: job_state_processing,
    artifactMap: buildArtifactMap(job_state_processing.artifacts),
  })
  const timersRef = useRef([])

  useEffect(() => {
    for (const [delayMs, eventType, payload] of MOCK_SSE_SEQUENCE) {
      const id = setTimeout(() => {
        dispatch({ type: 'EVENT', eventType, payload })
      }, delayMs)
      timersRef.current.push(id)
    }
    return () => {
      timersRef.current.forEach(clearTimeout)
      timersRef.current = []
    }
  }, [])

  return {
    job: state.job,
    artifacts: Object.values(state.artifactMap),
    isConnected: true,
    refetch: () => {},
  }
}

// ---------------------------------------------------------------------------
// Real API mode
// ---------------------------------------------------------------------------

function realReducer(state, action) {
  if (action.type === 'SET_FULL') {
    return { job: action.job, artifactMap: buildArtifactMap(action.job.artifacts ?? []) }
  }
  if (action.type === 'EVENT' && state.job) {
    const { job, artifactMap } = applyEvent(
      state.job, state.artifactMap, action.eventType, action.payload
    )
    return { job, artifactMap }
  }
  return state
}

function useRealJob(jobId) {
  const [{ job, artifactMap }, dispatch] = useReducer(realReducer, {
    job: null,
    artifactMap: {},
  })
  const [isConnected, setIsConnected] = useState(false)
  const esRef = useRef(null)
  const pollRef = useRef(null)

  const fetchJob = useCallback(async () => {
    if (!jobId) return
    try {
      const res = await fetch(`/api/jobs/${jobId}`)
      if (!res.ok) return
      const data = await res.json()
      dispatch({ type: 'SET_FULL', job: data })
    } catch {
      // silent — SSE is primary; polling is fallback
    }
  }, [jobId])

  useEffect(() => {
    if (!jobId) return

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
      dispatch({ type: 'EVENT', eventType, payload })
    }

    // artifact_ready carries only {artifact_id, type, index} — re-fetch for
    // file_url / text_content which are only on the REST endpoint.
    es.addEventListener('status_changed',  (e) => handleEvent('status_changed',  JSON.parse(e.data)))
    es.addEventListener('artifact_ready',  () => fetchJob())
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
  }, [jobId, fetchJob])

  return {
    job,
    artifacts: Object.values(artifactMap),
    isConnected,
    refetch: fetchJob,
  }
}

// ---------------------------------------------------------------------------
// Public hook
// ---------------------------------------------------------------------------

export default function useJob(jobId) {
  if (USE_REAL_API) {
    // Rules-of-hooks: flag is module-level constant; call order never changes.
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useRealJob(jobId)
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useMockJob()
}
