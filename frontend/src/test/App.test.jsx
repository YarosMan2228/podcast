import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from '../App.jsx'

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  )
}

describe('App routing', () => {
  test('renders LandingPage at /', () => {
    renderAt('/')
    expect(screen.getByText(/Full Content Pack/i)).toBeInTheDocument()
  })

  test('renders JobPage at /jobs/:jobId and shows the id', () => {
    renderAt('/jobs/abc-123')
    expect(screen.getByText(/abc-123/)).toBeInTheDocument()
  })

  test('unknown route renders nothing (no crash)', () => {
    // App has no catch-all — should not throw
    expect(() => renderAt('/does-not-exist')).not.toThrow()
  })
})
