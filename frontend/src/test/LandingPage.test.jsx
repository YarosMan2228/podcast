import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import LandingPage from '../pages/LandingPage.jsx'

function renderPage() {
  return render(
    <MemoryRouter>
      <LandingPage />
    </MemoryRouter>
  )
}

describe('LandingPage', () => {
  test('renders h1 headline', () => {
    renderPage()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Podcast/i)
  })

  test('renders the Dropzone (upload area)', () => {
    renderPage()
    expect(screen.getByRole('button', { name: /upload audio or video file/i })).toBeInTheDocument()
  })

  test('renders "how it works" steps', () => {
    renderPage()
    expect(screen.getByText('Upload your episode')).toBeInTheDocument()
    expect(screen.getByText('AI extracts the gold')).toBeInTheDocument()
    expect(screen.getByText('Download your content pack')).toBeInTheDocument()
  })
})
