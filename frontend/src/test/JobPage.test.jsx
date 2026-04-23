import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import JobPage from '../pages/JobPage.jsx'

function renderWithJobId(jobId) {
  return render(
    <MemoryRouter initialEntries={[`/jobs/${jobId}`]}>
      <Routes>
        <Route path="/jobs/:jobId" element={<JobPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('JobPage', () => {
  test('displays the jobId from URL params', () => {
    renderWithJobId('550e8400-e29b-41d4-a716-446655440000')
    expect(screen.getByText(/550e8400/)).toBeInTheDocument()
  })

  test('renders without crashing for any id', () => {
    expect(() => renderWithJobId('some-random-id')).not.toThrow()
  })
})
