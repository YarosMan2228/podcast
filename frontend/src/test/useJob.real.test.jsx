/**
 * useJob — real-API mode.
 *
 * The hook picks mock vs. real mode from `import.meta.env.MODE` at module
 * load, so each test stubs MODE and re-imports the module. EventSource and
 * fetch are replaced with minimal fakes.
 */
import { renderHook, act } from '@testing-library/react'
import { vi, describe, test, expect, beforeEach, afterEach } from 'vitest'

class FakeEventSource {
  constructor(url) {
    this.url = url
    this.listeners = {}
    this.closed = false
    FakeEventSource.instances.push(this)
  }
  addEventListener(type, fn) {
    ;(this.listeners[type] ||= []).push(fn)
  }
  emit(type, data) {
    for (const fn of this.listeners[type] ?? []) fn({ data: JSON.stringify(data) })
  }
  close() {
    this.closed = true
  }
}
FakeEventSource.instances = []

function snapshot({ status = 'COMPLETED', artifactStatus = 'READY', version = 1, packageUrl = '/media/packages/a.zip' }) {
  return {
    job_id: 'job-1',
    status,
    progress: { total_artifacts: 1, ready: artifactStatus === 'READY' ? 1 : 0, processing: 0, queued: artifactStatus === 'QUEUED' ? 1 : 0, failed: 0 },
    analysis: { episode_title: 'T', hook: 'H' },
    artifacts: [
      { id: 'art-1', type: 'LINKEDIN_POST', index: 0, status: artifactStatus, file_url: null, text_content: 'x', metadata: {}, version },
    ],
    package_url: packageUrl,
    error: null,
  }
}

async function loadHook() {
  vi.resetModules()
  const mod = await import('../hooks/useJob.js')
  return mod.default
}

describe('useJob real mode — regenerate on a COMPLETED job', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubEnv('MODE', 'development')
    FakeEventSource.instances = []
    globalThis.EventSource = FakeEventSource
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.useRealTimers()
    delete globalThis.EventSource
    delete globalThis.fetch
  })

  test('polls until the regenerated artifact is terminal again, then stops', async () => {
    const snapshots = [
      snapshot({}),                                   // initial GET
      snapshot({ artifactStatus: 'QUEUED', version: 2 }), // refetch after POST regenerate
      snapshot({ artifactStatus: 'PROCESSING', version: 2 }), // poll #1
      snapshot({ artifactStatus: 'READY', version: 2, packageUrl: '/media/packages/b.zip' }), // poll #2
    ]
    let call = 0
    globalThis.fetch = vi.fn(async () => {
      const body = snapshots[Math.min(call, snapshots.length - 1)]
      call += 1
      return { ok: true, json: async () => body }
    })

    const useJob = await loadHook()
    const { result } = renderHook(() => useJob('job-1'))

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.job.status).toBe('COMPLETED')
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    // Terminal on arrival → SSE socket closed.
    expect(FakeEventSource.instances[0].closed).toBe(true)

    // JobPage.handleRegenerate calls refetch() right after the 202.
    await act(async () => { await result.current.refetch() })
    expect(result.current.artifacts[0].status).toBe('QUEUED')
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)

    // Poll #1 → PROCESSING, still pending.
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(globalThis.fetch).toHaveBeenCalledTimes(3)
    expect(result.current.artifacts[0].status).toBe('PROCESSING')

    // Poll #2 → READY v2 with the re-packaged ZIP url.
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(globalThis.fetch).toHaveBeenCalledTimes(4)
    expect(result.current.artifacts[0].status).toBe('READY')
    expect(result.current.artifacts[0].version).toBe(2)
    expect(result.current.job.package_url).toBe('/media/packages/b.zip')

    // Nothing pending → polling stops.
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
    expect(globalThis.fetch).toHaveBeenCalledTimes(4)
  })

  test('does not poll a COMPLETED job when every artifact is terminal', async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => snapshot({}) }))
    const useJob = await loadHook()
    renderHook(() => useJob('job-1'))

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })
})
