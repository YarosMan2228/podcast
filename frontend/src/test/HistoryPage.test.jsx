import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, test, expect, beforeEach, afterEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import HistoryPage from '../pages/HistoryPage.jsx'

const JOBS = [
  {
    job_id: 'job-a', status: 'COMPLETED', source_type: 'file', original_filename: 'ep1.mp3',
    duration_sec: 1800, episode_title: 'First episode', hook: 'Hook A',
    progress: { total_artifacts: 12, ready: 12, failed: 0 }, has_package: true,
    error: null, created_at: '2026-09-01T10:00:00Z', completed_at: '2026-09-01T10:04:00Z',
  },
  {
    job_id: 'job-b', status: 'FAILED', source_type: 'url', source_url: 'https://youtu.be/x',
    duration_sec: null, episode_title: null, hook: null,
    progress: { total_artifacts: 0, ready: 0, failed: 0 }, has_package: false,
    error: 'TRANSCRIPTION_EMPTY: silent', created_at: '2026-09-02T10:00:00Z', completed_at: null,
  },
]

function mockFetch(handlers) {
  globalThis.fetch = vi.fn(async (url, opts = {}) => {
    const method = opts.method ?? 'GET'
    const handler = handlers[`${method} ${url}`]
    if (!handler) throw new Error(`unexpected ${method} ${url}`)
    const { status = 200, body } = handler()
    return { ok: status < 400, status, json: async () => body }
  })
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/history']}>
      <HistoryPage />
    </MemoryRouter>,
  )
}

describe('HistoryPage', () => {
  beforeEach(() => { delete globalThis.fetch })
  afterEach(() => { delete globalThis.fetch })

  test('lists jobs with title, status and error', async () => {
    mockFetch({ 'GET /api/jobs?limit=100': () => ({ body: { jobs: JOBS } }) })
    renderPage()
    expect(await screen.findByText('First episode')).toBeInTheDocument()
    expect(screen.getByText('https://youtu.be/x')).toBeInTheDocument()
    expect(screen.getByText('COMPLETED')).toBeInTheDocument()
    expect(screen.getByText('FAILED')).toBeInTheDocument()
    expect(screen.getByText('TRANSCRIPTION_EMPTY: silent')).toBeInTheDocument()
    // ZIP link only for the completed job with a package.
    expect(screen.getByLabelText('Download ZIP for First episode')).toHaveAttribute(
      'href', '/api/jobs/job-a/download',
    )
  })

  test('empty state', async () => {
    mockFetch({ 'GET /api/jobs?limit=100': () => ({ body: { jobs: [] } }) })
    renderPage()
    expect(await screen.findByText(/No episodes yet/)).toBeInTheDocument()
  })

  test('delete asks for confirmation, then removes the row', async () => {
    const user = userEvent.setup()
    mockFetch({
      'GET /api/jobs?limit=100': () => ({ body: { jobs: JOBS } }),
      'DELETE /api/jobs/job-a': () => ({ body: { deleted: true, job_id: 'job-a' } }),
    })
    renderPage()
    await screen.findByText('First episode')

    await user.click(screen.getByLabelText('Delete First episode'))
    await user.click(screen.getByLabelText('Confirm delete First episode'))

    await waitFor(() => expect(screen.queryByText('First episode')).not.toBeInTheDocument())
    expect(screen.getByText('https://youtu.be/x')).toBeInTheDocument()
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/jobs/job-a', { method: 'DELETE' })
  })

  test('shows API error with retry', async () => {
    mockFetch({
      'GET /api/jobs?limit=100': () => ({ status: 500, body: { error: { code: 'X', message: 'boom' } } }),
    })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})
