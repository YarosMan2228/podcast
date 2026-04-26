import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, test, expect, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import JobPage from '../pages/JobPage.jsx'

function renderWithJobId(jobId) {
  return render(
    <MemoryRouter initialEntries={[`/jobs/${jobId}`]}>
      <Routes>
        <Route path="/jobs/:jobId" element={<JobPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('JobPage', () => {
  test('renders without crashing', () => {
    expect(() => renderWithJobId('some-random-id')).not.toThrow()
  })

  test('shows episode title from mock data on initial render', () => {
    renderWithJobId('550e8400-e29b-41d4-a716-446655440000')
    expect(screen.getByText('The Hidden Cost of AI Hype')).toBeInTheDocument()
  })

  test('shows progress bar while job is processing', () => {
    renderWithJobId('any-id')
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
  })

  test('progress bar has correct ARIA attributes', () => {
    renderWithJobId('any-id')
    const bar = screen.getByRole('progressbar')
    expect(bar).toHaveAttribute('aria-valuemin', '0')
    expect(bar).toHaveAttribute('aria-valuemax', '100')
    // GENERATING is phase index 3 out of 5 → 60 %
    expect(bar).toHaveAttribute('aria-valuenow', '60')
  })
})

describe('JobPage — results branch (completed state)', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  test('shows Download All button after completion', () => {
    renderWithJobId('any-id')
    act(() => { vi.runAllTimers() })
    expect(screen.getByText('Download All (ZIP)')).toBeInTheDocument()
  })

  test('hides progress bar after completion', () => {
    renderWithJobId('any-id')
    act(() => { vi.runAllTimers() })
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  test('shows artifact sections after completion', () => {
    renderWithJobId('any-id')
    act(() => { vi.runAllTimers() })
    // At least one text section heading should be visible
    expect(screen.getByText('LinkedIn')).toBeInTheDocument()
    expect(screen.getByText('Video Clips')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// FAILED branch — rendered straight from a stubbed useJob, doesn't depend on
// the mock SSE timeline.
// ---------------------------------------------------------------------------

describe('JobPage — FAILED branch', () => {
  beforeEach(() => {
    vi.resetModules()
  })
  afterEach(() => {
    vi.doUnmock('../hooks/useJob.js')
  })

  async function renderFailedJob(error) {
    vi.doMock('../hooks/useJob.js', () => ({
      default: () => ({
        job: {
          job_id: 'failed-job',
          status: 'FAILED',
          progress: { total_artifacts: 0, ready: 0, processing: 0, queued: 0, failed: 0 },
          analysis: null,
          artifacts: [],
          package_url: null,
          error,
        },
        artifacts: [],
        isConnected: true,
        refetch: vi.fn(),
      }),
    }))
    const { default: JobPageFresh } = await import('../pages/JobPage.jsx')
    return render(
      <MemoryRouter initialEntries={['/jobs/x']}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobPageFresh />} />
          <Route path="/" element={<div>landing</div>} />
        </Routes>
      </MemoryRouter>,
    )
  }

  test('renders red "Processing failed" headline with the persisted error', async () => {
    await renderFailedJob('TRANSCRIPTION_INVALID_INPUT: Error code: 401 - Bad key')
    expect(screen.getByRole('alert')).toHaveTextContent('Processing failed')
    expect(screen.getByText(/TRANSCRIPTION_INVALID_INPUT/)).toBeInTheDocument()
    expect(screen.getByText(/Error code: 401/)).toBeInTheDocument()
  })

  test('falls back to a generic message when error is null', async () => {
    await renderFailedJob(null)
    expect(screen.getByText(/An unexpected error occurred/)).toBeInTheDocument()
  })

  test('"Try again" button navigates back to landing without a full reload', async () => {
    const user = userEvent.setup()
    await renderFailedJob('boom')
    const btn = screen.getByRole('button', { name: /try again/i })
    await user.click(btn)
    // SPA navigation: the landing-route stub is now in the DOM, no document reload.
    expect(screen.getByText('landing')).toBeInTheDocument()
  })

  test('does NOT render "Connection lost" banner on FAILED', async () => {
    await renderFailedJob('boom')
    expect(screen.queryByText(/Connection lost/i)).not.toBeInTheDocument()
  })

  test('does NOT render the progress bar on FAILED', async () => {
    await renderFailedJob('boom')
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })
})
