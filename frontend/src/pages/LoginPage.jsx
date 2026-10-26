import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { ApiError } from '../lib/api'
import { usePublicTheme } from '../lib/site'

export default function LoginPage() {
  const { user, loading, login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [needsTotp, setNeedsTotp] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  // Applies the stored or system theme; without this the login page stayed light.
  usePublicTheme()

  if (loading) return null
  if (user) {
    const target = location.state?.from?.pathname || '/console'
    return <Navigate to={target} replace />
  }

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await login(email, password, needsTotp ? totp : '')
      navigate('/console', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.code === 'totp_required') {
        setNeedsTotp(true)
        setError('Enter the 6-digit code from your authenticator app.')
      } else if (err instanceof ApiError && err.code === 'invalid_totp') {
        setError('That code is not valid. Check your authenticator app.')
      } else {
        setError(err.message || 'Sign-in failed.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-full grid place-items-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-8 w-8 rounded-md bg-accent text-accent-fg grid place-items-center font-bold">
            G
          </div>
          <div>
            <div className="font-semibold tracking-tight">Gateway Console</div>
            <div className="text-xs text-muted">Private, invite-only access</div>
          </div>
        </div>

        <form onSubmit={submit} className="card p-5 space-y-4">
          <label className="block">
            <span className="label">Email</span>
            <input
              className="input"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoFocus
            />
          </label>

          <label className="block">
            <span className="label">Password</span>
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>

          {needsTotp && (
            <label className="block">
              <span className="label">Two-factor code</span>
              <input
                className="input mono tracking-[0.3em] text-center"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="000000"
                value={totp}
                onChange={(event) => setTotp(event.target.value.replace(/\D/g, ''))}
                autoFocus
              />
            </label>
          )}

          {error && (
            <p className="text-xs text-danger" role="alert">
              {error}
            </p>
          )}

          <button className="btn-primary w-full" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="mt-4 text-xs text-muted text-center">
          Accounts are created by the owner. There is no public sign-up.
        </p>
      </div>
    </div>
  )
}
