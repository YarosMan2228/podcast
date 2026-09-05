import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, test, expect, beforeEach } from 'vitest'
import BrandingSettings from '../components/BrandingSettings.jsx'
import { loadStoredBranding, DEFAULT_BRANDING } from '../api/branding.js'
import { uploadFile, submitUrl } from '../api/client.js'

/** Controlled-component harness — mirrors how LandingPage owns the state. */
function Harness({ initial, onChange }) {
  const [value, setValue] = useState(initial)
  return (
    <BrandingSettings
      value={value}
      onChange={(next) => { setValue(next); onChange?.(next) }}
    />
  )
}

describe('BrandingSettings', () => {
  beforeEach(() => {
    localStorage.clear()
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:preview')
    globalThis.URL.revokeObjectURL = vi.fn()
  })

  test('collapsed by default, expands and edits name + colour', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness initial={{ ...DEFAULT_BRANDING, logo: null }} onChange={onChange} />)

    expect(screen.queryByLabelText('Podcast name')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /branding/i }))
    await user.type(screen.getByLabelText('Podcast name'), 'My')

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ podcast_name: 'My' }))
    // Persisted for the next visit (name only — the logo File can't be stored).
    expect(loadStoredBranding().podcast_name).toBe('My')
  })

  test('rejects a non-image logo', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const onChange = vi.fn()
    render(<Harness initial={{ podcast_name: 'X', brand_color: '#123456', logo: null }} onChange={onChange} />)
    const bad = new File(['%PDF'], 'doc.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('Logo file'), bad)
    expect(screen.getByRole('alert')).toHaveTextContent(/PNG, JPEG, WebP or SVG/)
    expect(onChange).not.toHaveBeenCalled()
  })

  test('accepts a PNG logo and shows a preview with remove', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness initial={{ podcast_name: 'X', brand_color: '#123456', logo: null }} onChange={onChange} />)
    const logo = new File(['png'], 'logo.png', { type: 'image/png' })
    await user.upload(screen.getByLabelText('Logo file'), logo)
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ logo }))
    expect(screen.getByAltText('Logo preview')).toHaveAttribute('src', 'blob:preview')
    await user.click(screen.getByLabelText('Remove logo'))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ logo: null }))
  })

  test('loadStoredBranding ignores garbage', () => {
    localStorage.setItem('podcastpack.branding', '{"brand_color":"red","podcast_name":5}')
    expect(loadStoredBranding()).toEqual(DEFAULT_BRANDING)
  })
})

describe('client sends branding', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ job_id: 'j', status: 'PENDING' }) }))
  })

  test('uploadFile appends podcast_name, brand_color and logo', async () => {
    const logo = new File(['x'], 'logo.png', { type: 'image/png' })
    await uploadFile(new File(['a'], 'ep.mp3', { type: 'audio/mpeg' }), {
      podcast_name: 'Show', brand_color: '#00ff00', logo,
    })
    const [, opts] = globalThis.fetch.mock.calls[0]
    const form = opts.body
    expect(form.get('podcast_name')).toBe('Show')
    expect(form.get('brand_color')).toBe('#00ff00')
    expect(form.get('logo')).toBe(logo)
  })

  test('submitUrl uses JSON without a logo and multipart with one', async () => {
    await submitUrl('https://youtu.be/x', { podcast_name: 'S', brand_color: '#111111', logo: null })
    let [, opts] = globalThis.fetch.mock.calls[0]
    expect(JSON.parse(opts.body)).toEqual({ url: 'https://youtu.be/x', podcast_name: 'S', brand_color: '#111111' })

    const logo = new File(['x'], 'logo.png', { type: 'image/png' })
    await submitUrl('https://youtu.be/x', { logo })
    ;[, opts] = globalThis.fetch.mock.calls[1]
    expect(opts.body).toBeInstanceOf(FormData)
    expect(opts.body.get('url')).toBe('https://youtu.be/x')
    expect(opts.body.get('logo')).toBe(logo)
  })
})
