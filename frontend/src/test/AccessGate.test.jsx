import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, test, expect, afterEach } from 'vitest'
import AccessGate from '../components/AccessGate.jsx'

function mockFetch(handlers) {
  globalThis.fetch = vi.fn(async (url, opts = {}) => {
    const method = opts.method ?? 'GET'
    const h = handlers[`${method} ${url}`]
    if (!h) throw new Error(`unexpected ${method} ${url}`)
    const { status = 200, body } = h(opts)
    return { ok: status < 400, status, json: async () => body }
  })
}

describe('AccessGate', () => {
  afterEach(() => { delete globalThis.fetch })

  test('renders children immediately when the server has no token', async () => {
    mockFetch({ 'GET /api/auth/session': () => ({ body: { required: false, authenticated: true } }) })
    render(<AccessGate><p>app</p></AccessGate>)
    expect(await screen.findByText('app')).toBeInTheDocument()
  })

  test('shows the token form, rejects a wrong token, then lets a right one in', async () => {
    const user = userEvent.setup()
    mockFetch({
      'GET /api/auth/session': () => ({ body: { required: true, authenticated: false } }),
      'POST /api/auth/session': (opts) => {
        const { token } = JSON.parse(opts.body)
        return token === 'good'
          ? { body: { authenticated: true } }
          : { status: 401, body: { error: { code: 'AUTH_INVALID', message: 'Access token is not valid.' } } }
      },
    })
    render(<AccessGate><p>app</p></AccessGate>)
    const input = await screen.findByLabelText('Access token')
    expect(screen.queryByText('app')).not.toBeInTheDocument()

    await user.type(input, 'bad')
    await user.click(screen.getByRole('button', { name: /enter/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong token')

    await user.clear(input)
    await user.type(input, 'good')
    await user.click(screen.getByRole('button', { name: /enter/i }))
    expect(await screen.findByText('app')).toBeInTheDocument()
  })

  test('fails open if the probe itself errors (backend down)', async () => {
    globalThis.fetch = vi.fn(async () => { throw new Error('ECONNREFUSED') })
    render(<AccessGate><p>app</p></AccessGate>)
    expect(await screen.findByText('app')).toBeInTheDocument()
  })
})
