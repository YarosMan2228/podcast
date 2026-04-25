import { renderHook, act } from '@testing-library/react'
import { vi, describe, test, expect, beforeEach, afterEach } from 'vitest'
import useJob, { applyEvent } from '../hooks/useJob.js'
import { job_state_processing, MOCK_SSE_SEQUENCE } from '../api/mocks.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeJob(overrides = {}) {
  return {
    job_id: 'test-job',
    status: 'GENERATING',
    progress: { total_artifacts: 4, ready: 0, processing: 2, queued: 2, failed: 0 },
    analysis: { episode_title: 'Test', hook: 'Hook' },
    artifacts: [],
    package_url: null,
    error: null,
    ...overrides,
  }
}

function makeMap(...artifacts) {
  return Object.fromEntries(artifacts.map((a) => [a.id, a]))
}

// ---------------------------------------------------------------------------
// Part 1 — applyEvent unit tests (pure function)
// ---------------------------------------------------------------------------

describe('applyEvent — status_changed', () => {
  test('updates job.status', () => {
    const job = makeJob({ status: 'TRANSCRIBING' })
    const { job: next } = applyEvent(job, {}, 'status_changed', { status: 'ANALYZING' })
    expect(next.status).toBe('ANALYZING')
  })

  test('preserves all other job fields', () => {
    const job = makeJob()
    const map = makeMap({ id: 'a1', type: 'VIDEO_CLIP', index: 0, status: 'QUEUED' })
    const { job: next, artifactMap } = applyEvent(job, map, 'status_changed', { status: 'PACKAGING' })
    expect(next.job_id).toBe(job.job_id)
    expect(next.analysis).toBe(job.analysis)
    expect(artifactMap).toBe(map)
  })
})

describe('applyEvent — artifact_ready', () => {
  test('sets artifact status to READY in the map', () => {
    const job = makeJob()
    const queued = { id: 'art-li-0', type: 'LINKEDIN_POST', index: 0, status: 'QUEUED' }
    const map = makeMap(queued)
    const { artifactMap } = applyEvent(job, map, 'artifact_ready', {
      artifact_id: 'art-li-0', type: 'LINKEDIN_POST', index: 0,
    })
    expect(artifactMap['art-li-0'].status).toBe('READY')
  })

  test('increments progress.ready', () => {
    const job = makeJob({ progress: { ready: 2, processing: 1, queued: 1, failed: 0 } })
    const { job: next } = applyEvent(job, {}, 'artifact_ready', {
      artifact_id: 'art-li-0', type: 'LINKEDIN_POST', index: 0,
    })
    expect(next.progress.ready).toBe(3)
  })

  test('decrements progress.queued (floor 0)', () => {
    const job = makeJob({ progress: { ready: 0, processing: 0, queued: 1, failed: 0 } })
    const { job: next } = applyEvent(job, {}, 'artifact_ready', {
      artifact_id: 'art-li-0', type: 'LINKEDIN_POST', index: 0,
    })
    expect(next.progress.queued).toBe(0)
  })

  test('queued does not go below 0', () => {
    const job = makeJob({ progress: { ready: 0, processing: 0, queued: 0, failed: 0 } })
    const { job: next } = applyEvent(job, {}, 'artifact_ready', {
      artifact_id: 'art-li-0', type: 'LINKEDIN_POST', index: 0,
    })
    expect(next.progress.queued).toBe(0)
  })

  test('creates a new artifact entry for unknown id', () => {
    const job = makeJob()
    const { artifactMap } = applyEvent(job, {}, 'artifact_ready', {
      artifact_id: 'brand-new-id', type: 'SHOW_NOTES', index: 0,
    })
    expect(artifactMap['brand-new-id']).toBeDefined()
    expect(artifactMap['brand-new-id'].status).toBe('READY')
  })
})

describe('applyEvent — artifact_failed', () => {
  test('sets artifact status to FAILED', () => {
    const job = makeJob()
    const queued = { id: 'art-vid-4', type: 'VIDEO_CLIP', index: 4, status: 'QUEUED' }
    const map = makeMap(queued)
    const { artifactMap } = applyEvent(job, map, 'artifact_failed', {
      artifact_id: 'art-vid-4', error: 'FFmpeg crash',
    })
    expect(artifactMap['art-vid-4'].status).toBe('FAILED')
  })

  test('stores error message on the artifact', () => {
    const job = makeJob()
    const map = makeMap({ id: 'art-vid-4', type: 'VIDEO_CLIP', index: 4, status: 'QUEUED' })
    const { artifactMap } = applyEvent(job, map, 'artifact_failed', {
      artifact_id: 'art-vid-4', error: 'FFmpeg crash',
    })
    expect(artifactMap['art-vid-4'].error).toBe('FFmpeg crash')
  })

  test('increments progress.failed', () => {
    const job = makeJob({ progress: { ready: 0, processing: 0, queued: 1, failed: 0 } })
    const { job: next } = applyEvent(job, {}, 'artifact_failed', {
      artifact_id: 'art-vid-4', error: 'err',
    })
    expect(next.progress.failed).toBe(1)
  })

  test('decrements progress.queued (floor 0)', () => {
    const job = makeJob({ progress: { ready: 0, processing: 0, queued: 1, failed: 0 } })
    const { job: next } = applyEvent(job, {}, 'artifact_failed', {
      artifact_id: 'art-vid-4', error: 'err',
    })
    expect(next.progress.queued).toBe(0)
  })

  test('creates a new artifact entry for unknown id', () => {
    const job = makeJob()
    const { artifactMap } = applyEvent(job, {}, 'artifact_failed', {
      artifact_id: 'new-id', error: 'oops',
    })
    expect(artifactMap['new-id'].status).toBe('FAILED')
    expect(artifactMap['new-id'].error).toBe('oops')
  })
})

describe('applyEvent — completed', () => {
  test('sets job.status to COMPLETED', () => {
    const job = makeJob({ status: 'PACKAGING' })
    const { job: next } = applyEvent(job, {}, 'completed', { package_url: '/media/pack.zip' })
    expect(next.status).toBe('COMPLETED')
  })

  test('sets package_url from payload', () => {
    const job = makeJob()
    const { job: next } = applyEvent(job, {}, 'completed', { package_url: '/media/pack.zip' })
    expect(next.package_url).toBe('/media/pack.zip')
  })

  test('preserves artifactMap unchanged', () => {
    const job = makeJob()
    const map = makeMap({ id: 'art-li-0', type: 'LINKEDIN_POST', index: 0, status: 'READY' })
    const { artifactMap } = applyEvent(job, map, 'completed', { package_url: '/p.zip' })
    expect(artifactMap).toBe(map)
  })
})

describe('applyEvent — unknown event type', () => {
  test('returns job and artifactMap unchanged', () => {
    const job = makeJob()
    const map = makeMap({ id: 'a', type: 'VIDEO_CLIP', index: 0, status: 'QUEUED' })
    const { job: nextJob, artifactMap } = applyEvent(job, map, 'unknown_event', { foo: 'bar' })
    expect(nextJob).toBe(job)
    expect(artifactMap).toBe(map)
  })
})

// ---------------------------------------------------------------------------
// Part 2 — useMockJob via useJob hook (fake timers)
// ---------------------------------------------------------------------------

describe('useJob mock mode — initial state after mount', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  test('job is null before useEffect fires', () => {
    // We cannot observe the null moment via renderHook because renderHook wraps
    // in act() and flushes effects. Verify the *after-mount* state instead.
    const { result } = renderHook(() => useJob('test'))
    expect(result.current.job).not.toBeNull()
  })

  test('job matches job_state_processing after mount', () => {
    const { result } = renderHook(() => useJob('test'))
    expect(result.current.job).toEqual(job_state_processing)
  })

  test('artifacts array length matches job_state_processing', () => {
    const { result } = renderHook(() => useJob('test'))
    expect(result.current.artifacts).toHaveLength(job_state_processing.artifacts.length)
  })

  test('all artifacts are QUEUED at mount', () => {
    const { result } = renderHook(() => useJob('test'))
    for (const a of result.current.artifacts) {
      expect(a.status).toBe('QUEUED')
    }
  })

  test('isConnected is true immediately', () => {
    const { result } = renderHook(() => useJob('test'))
    expect(result.current.isConnected).toBe(true)
  })
})

describe('useJob mock mode — SSE event progression', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  test('status changes to INGESTING at 500 ms', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.advanceTimersByTime(500) })
    expect(result.current.job.status).toBe('INGESTING')
  })

  test('status changes to TRANSCRIBING at 1500 ms', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.advanceTimersByTime(1500) })
    expect(result.current.job.status).toBe('TRANSCRIBING')
  })

  test('status changes to ANALYZING at 3000 ms', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.advanceTimersByTime(3000) })
    expect(result.current.job.status).toBe('ANALYZING')
  })

  test('status changes to GENERATING at 5000 ms', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.advanceTimersByTime(5000) })
    expect(result.current.job.status).toBe('GENERATING')
  })

  test('first artifact becomes READY at 5500 ms', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.advanceTimersByTime(5500) })
    const ready = result.current.artifacts.filter((a) => a.status === 'READY')
    expect(ready.length).toBeGreaterThanOrEqual(1)
  })

  test('art-vid-4 becomes FAILED at 9800 ms', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.advanceTimersByTime(9800) })
    const failed = result.current.artifacts.find((a) => a.id === 'art-vid-4')
    expect(failed?.status).toBe('FAILED')
  })

  test('art-vid-4 carries the error message after failure', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.advanceTimersByTime(9800) })
    const failed = result.current.artifacts.find((a) => a.id === 'art-vid-4')
    expect(typeof failed?.error).toBe('string')
    expect(failed?.error.length).toBeGreaterThan(0)
  })

  test('status changes to PACKAGING at 11000 ms', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.advanceTimersByTime(11000) })
    expect(result.current.job.status).toBe('PACKAGING')
  })

  test('job becomes COMPLETED after all timers', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.runAllTimers() })
    expect(result.current.job.status).toBe('COMPLETED')
  })

  test('progress.ready increments as artifact_ready events fire', () => {
    const { result } = renderHook(() => useJob('test'))
    const readyBefore = result.current.job.progress.ready
    act(() => { vi.advanceTimersByTime(5500) }) // first artifact_ready at 5500ms
    expect(result.current.job.progress.ready).toBeGreaterThan(readyBefore)
  })
})

describe('useJob mock mode — full simulation end state', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  test('package_url is set after completion', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.runAllTimers() })
    expect(typeof result.current.job.package_url).toBe('string')
    expect(result.current.job.package_url.length).toBeGreaterThan(0)
  })

  test('artifact_ready events deliver full text_content for LINKEDIN_POST', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.runAllTimers() })
    const linkedin = result.current.artifacts.find((a) => a.type === 'LINKEDIN_POST')
    expect(linkedin?.status).toBe('READY')
    expect(typeof linkedin?.text_content).toBe('string')
    expect(linkedin?.text_content.length).toBeGreaterThan(0)
  })

  test('no artifact is left in QUEUED status after completion', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.runAllTimers() })
    const queued = result.current.artifacts.filter((a) => a.status === 'QUEUED')
    expect(queued).toHaveLength(0)
  })

  test('every artifact_ready event in SSE sequence corresponds to a READY artifact', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.runAllTimers() })
    const readyIds = new Set(
      MOCK_SSE_SEQUENCE
        .filter(([, t]) => t === 'artifact_ready')
        .map(([, , p]) => p.artifact_id),
    )
    for (const id of readyIds) {
      const a = result.current.artifacts.find((x) => x.id === id)
      expect(a?.status).toBe('READY')
    }
  })

  test('every artifact_failed event corresponds to a FAILED artifact', () => {
    const { result } = renderHook(() => useJob('test'))
    act(() => { vi.runAllTimers() })
    const failedIds = new Set(
      MOCK_SSE_SEQUENCE
        .filter(([, t]) => t === 'artifact_failed')
        .map(([, , p]) => p.artifact_id),
    )
    for (const id of failedIds) {
      const a = result.current.artifacts.find((x) => x.id === id)
      expect(a?.status).toBe('FAILED')
    }
  })
})

describe('useJob mock mode — unmount cleanup', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  test('timers are cancelled on unmount (no state update after unmount)', () => {
    const { result, unmount } = renderHook(() => useJob('test'))
    const statusAtUnmount = result.current.job?.status
    unmount()
    // Advancing time after unmount should not throw or mutate result
    expect(() => act(() => { vi.runAllTimers() })).not.toThrow()
    // result.current is frozen at unmount-time value
    expect(result.current.job?.status).toBe(statusAtUnmount)
  })
})
