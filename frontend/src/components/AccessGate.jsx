import { useEffect, useState } from 'react'
import { getSession, loginWithToken } from '../api/client.js'

/**
 * Blocks the SPA behind the server's optional access token.
 * When APP_ACCESS_TOKEN is unset on the server the probe answers
 * `required: false` and children render immediately.
 */
export default function AccessGate({ children }) {
  const [state, setState] = useState({ checked: false, required: false, authenticated: false })
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    getSession()
      .then((s) => { if (!cancelled) setState({ checked: true, required: s.required, authenticated: s.authenticated }) })
      // If the probe itself fails (backend down), don't lock the user out
      // of the UI — pages show their own connection errors.
      .catch(() => { if (!cancelled) setState({ checked: true, required: false, authenticated: true }) })
    return () => { cancelled = true }
  }, [])

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await loginWithToken(token.trim())
      setState((s) => ({ ...s, authenticated: true }))
    } catch (err) {
      setError(err.code === 'AUTH_INVALID' ? 'Wrong token' : (err.message ?? 'Login failed'))
    } finally {
      setBusy(false)
    }
  }

  if (!state.checked) {
    return (
      <div className="min-h-screen flex items-center justify-center" role="status" aria-label="Checking access">
        <div className="w-8 h-8 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (state.required && !state.authenticated) {
    return (
      <main className="min-h-screen flex items-center justify-center px-4 bg-gray-50">
        <form onSubmit={submit} className="w-full max-w-sm bg-white border border-gray-200 rounded-2xl p-6 space-y-4">
          <h1 className="text-lg font-bold text-gray-900">Access token</h1>
          <p className="text-sm text-gray-500">This server is private. Paste the token from <code>.env</code> (APP_ACCESS_TOKEN).</p>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="current-password"
            aria-label="Access token"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={busy || !token.trim()}
            className="w-full bg-indigo-600 text-white py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? 'Checking…' : 'Enter'}
          </button>
        </form>
      </main>
    )
  }

  return children
}
