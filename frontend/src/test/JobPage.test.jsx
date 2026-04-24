import { render, screen, act } from '@testing-library/react'
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
