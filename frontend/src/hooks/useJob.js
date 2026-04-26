import { useState, useReducer, useEffect, useRef, useCallback } from 'react'
import { job_state_processing, MOCK_SSE_SEQUENCE } from '../api/mocks.js'

// Real API in dev/prod; mock mode under Vitest so the existing mock-driven
// tests (JobPage, useJob mock-mode, App routing) keep working without having
// to stub fetch + EventSource everywhere.
const USE_REAL_API = import.meta.env.MODE !== 'test'

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

// Terminal job statuses — once the row reaches one of these, no more
// events can arrive, polling has no point, and the SSE socket should be
// closed (otherwise it stays open until the server times it out).
const TERMINAL_STATUSES = new Set(['COMPLETED', 'FAILED'])

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
  // Ref we read inside the SSE onerror handler. Stored as a ref (not a
  // dep) so the effect re-runs only on jobId change, not on every status
  // update — which would tear down + reopen the SSE socket constantly.
  const terminalRef = useRef(false)
  terminalRef.current = TERMINAL_STATUSES.has(job?.status)

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const closeStream = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
    stopPolling()
  }, [stopPolling])

  const fetchJob = useCallback(async () => {
    if (!jobId) return
    try {
      const res = await fetch(`/api/jobs/${jobId}`)
      if (!res.ok) return
      const data = await res.json()
      dispatch({ type: 'SET_FULL', job: data })
      // If the polled snapshot says we're terminal, stop polling + drop
      // the (likely already dead) SSE connection. Otherwise the fallback
      // would keep refetching the same FAILED row forever.
      if (TERMINAL_STATUSES.has(data?.status)) {
        closeStream()
      }
    } catch {
      // silent — SSE is primary; polling is fallback
    }
  }, [jobId, closeStream])

  // Tear down everything when status flips to terminal (covers the SSE
  // path: a job_failed/completed event arrives → reducer marks status →
  // this effect catches it and closes the socket cleanly).
  useEffect(() => {
    if (TERMINAL_STATUSES.has(job?.status)) {
      closeStream()
    }
  }, [job?.status, closeStream])

  useEffect(() => {
    if (!jobId) return

    fetchJob()

    const es = new EventSource(`/api/jobs/${jobId}/events`)
    esRef.current = es

    es.onopen = () => {
      setIsConnected(true)
      stopPolling()
    }

    es.onerror = () => {
      // Don't escalate to polling if the job is already terminal — the
      // SSE close is *expected* in that case (server emits job_failed /
      // completed and disconnects). Without this guard the UI would
      // flash "Connection lost — polling for updates" the moment a job
      // fails, which looks like a system error to the user.
      if (terminalRef.current) {
        setIsConnected(true) // silence the banner
        stopPolling()
        return
      }
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
    // job_failed payload = {status, code, error}. We refetch to pick up
    // the persisted error string (server writes Job.error before publish),
    // which gives JobPage everything it needs to render the FAILED card
    // without a second poll.
    es.addEventListener('job_failed', () => fetchJob())

    return () => {
      es.close()
      esRef.current = null
      stopPolling()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, fetchJob, stopPolling])

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
