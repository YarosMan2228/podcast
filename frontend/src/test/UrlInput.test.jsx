import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, test, expect, vi } from 'vitest'
import UrlInput from '../components/UrlInput.jsx'

function renderInput(onSubmit = vi.fn()) {
  return { onSubmit, ...render(<UrlInput onSubmit={onSubmit} />) }
}

describe('UrlInput — idle state', () => {
  test('renders URL input field', () => {
    renderInput()
    expect(screen.getByRole('textbox', { name: /podcast url/i })).toBeInTheDocument()
  })

  test('renders submit button', () => {
    renderInput()
    expect(screen.getByRole('button', { name: /go/i })).toBeInTheDocument()
  })

  test('submit button is disabled when input is empty', () => {
    renderInput()
    expect(screen.getByRole('button', { name: /go/i })).toBeDisabled()
  })
})

describe('UrlInput — valid URL submission', () => {
  test('calls onSubmit with the trimmed URL for a valid https URL', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderInput()
    await user.type(screen.getByRole('textbox'), 'https://youtube.com/watch?v=abc')
    await user.click(screen.getByRole('button', { name: /go/i }))
    expect(onSubmit).toHaveBeenCalledWith('https://youtube.com/watch?v=abc')
  })

  test('submit button is enabled after typing a URL', async () => {
    const user = userEvent.setup()
    renderInput()
    await user.type(screen.getByRole('textbox'), 'https://example.com')
    expect(screen.getByRole('button', { name: /go/i })).not.toBeDisabled()
  })
})

describe('UrlInput — invalid URL', () => {
  test('shows error for non-URL text', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderInput()
    await user.type(screen.getByRole('textbox'), 'not a url at all')
    await user.click(screen.getByRole('button', { name: /go/i }))
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  test('error clears when user starts typing again', async () => {
    const user = userEvent.setup()
    renderInput()
    await user.type(screen.getByRole('textbox'), 'bad input')
    await user.click(screen.getByRole('button', { name: /go/i }))
    expect(screen.getByRole('alert')).toBeInTheDocument()
    await user.type(screen.getByRole('textbox'), 'x')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
