import { useEffect, useRef, useState } from 'react'
import {
  AUTH_TOKEN_KEY,
  authConfig,
  loginAccount,
  loginGoogle,
  signupAccount,
} from '../api'
import type { AuthUser } from '../types'

interface AuthModalProps {
  open: boolean
  onClose: () => void
  onAuthenticated: (user: AuthUser) => void
}

type Mode = 'signin' | 'signup' | 'inbox'

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string
            callback: (response: { credential: string }) => void
          }) => void
          renderButton: (
            el: HTMLElement,
            options: Record<string, string | number>,
          ) => void
        }
      }
    }
  }
}

export function AuthModal({ open, onClose, onAuthenticated }: AuthModalProps) {
  const [mode, setMode] = useState<Mode>('signin')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [inbox, setInbox] = useState<{
    to: string
    from: string
    subject: string
    verify_url: string
    sent?: boolean
    send_error?: string
  } | null>(null)
  const [googleId, setGoogleId] = useState('')
  const googleRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    authConfig()
      .then((cfg) => setGoogleId(cfg.google_client_id || ''))
      .catch(() => setGoogleId(''))
  }, [open])

  useEffect(() => {
    if (!open || !googleId || mode === 'inbox') return
    const existing = document.getElementById('google-gsi')
    const start = () => {
      if (!window.google || !googleRef.current) return
      googleRef.current.innerHTML = ''
      window.google.accounts.id.initialize({
        client_id: googleId,
        callback: (response) => {
          void loginGoogle(response.credential)
            .then((data) => {
              localStorage.setItem(AUTH_TOKEN_KEY, data.token)
              onAuthenticated(data.user)
            })
            .catch((err: unknown) => {
              setError(err instanceof Error ? err.message : 'Google sign-in failed')
            })
        },
      })
      window.google.accounts.id.renderButton(googleRef.current, {
        theme: 'filled_black',
        size: 'large',
        shape: 'pill',
        text: 'continue_with',
        width: 320,
      })
    }
    if (window.google) {
      start()
      return
    }
    if (existing) {
      existing.addEventListener('load', start)
      return () => existing.removeEventListener('load', start)
    }
    const script = document.createElement('script')
    script.id = 'google-gsi'
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.onload = start
    document.head.appendChild(script)
  }, [open, googleId, mode, onAuthenticated])

  if (!open) return null

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'signup') {
        const result = await signupAccount(email.trim(), password, name.trim())
        setInbox({
          to: result.email,
          from: result.preview?.from || 'Sentinel',
          subject: result.preview?.subject || 'Verify your Sentinel email',
          verify_url: result.preview?.verify_url || result.verify_url || '',
          sent: result.email_sent,
          send_error: result.send_error,
        })
        setMode('inbox')
      } else {
        const data = await loginAccount(email.trim(), password)
        localStorage.setItem(AUTH_TOKEN_KEY, data.token)
        onAuthenticated(data.user)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not continue')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-scrim" role="presentation" onClick={onClose}>
      <div
        className="auth-modal"
        role="dialog"
        aria-labelledby="auth-title"
        onClick={(event) => event.stopPropagation()}
      >
        {mode === 'inbox' && inbox ? (
          <>
            <p className="auth-kicker">Verification sent</p>
            <h2 id="auth-title">Check your email</h2>
            <p className="muted">
              {inbox.sent ? (
                <>
                  We sent a confirmation link to <strong>{inbox.to}</strong>.
                  Open it to activate your account.
                </>
              ) : (
                <>
                  Account created for <strong>{inbox.to}</strong>, but a real
                  email was not sent yet
                  {inbox.send_error ? ` (${inbox.send_error})` : ''}. Use the
                  button below, then add <code>RESEND_API_KEY</code> to{' '}
                  <code>.env</code>.
                </>
              )}
            </p>
            <article className="email-preview">
              <div className="email-meta">
                <span>From {inbox.from}</span>
                <span>To {inbox.to}</span>
              </div>
              <h3>{inbox.subject}</h3>
              <p>
                Welcome to Sentinel. Confirm this address so we know it is
                yours.
              </p>
              {inbox.verify_url ? (
                <a className="btn btn-primary" href={inbox.verify_url}>
                  Verify email address
                </a>
              ) : (
                <p className="muted">Open the message in your inbox to continue.</p>
              )}
            </article>
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Close
            </button>
          </>
        ) : (
          <>
            <p className="auth-kicker">
              {mode === 'signup' ? 'Create an account' : 'Welcome back'}
            </p>
            <h2 id="auth-title">
              {mode === 'signup' ? 'Sign up' : 'Log in'}
            </h2>
            <div ref={googleRef} className="google-slot" />
            {!googleId ? (
              <p className="muted google-hint">
                Sign in with Google is available once a Google client ID is
                added. Use email to get started.
              </p>
            ) : null}
            <div className="auth-or">or continue with email</div>
            <form className="auth-form" onSubmit={(e) => void handleSubmit(e)}>
              {mode === 'signup' ? (
                <label>
                  Name
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    autoComplete="name"
                    placeholder="Ada Lovelace"
                  />
                </label>
              ) : null}
              <label>
                Email
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  required
                  placeholder="you@company.com"
                />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={
                    mode === 'signup' ? 'new-password' : 'current-password'
                  }
                  required
                  minLength={mode === 'signup' ? 8 : 1}
                  placeholder={mode === 'signup' ? 'At least 8 characters' : '••••••••'}
                />
              </label>
              {error ? <p className="error-banner">{error}</p> : null}
              <button
                type="submit"
                className="btn btn-primary"
                disabled={busy}
              >
                {busy
                  ? 'Working…'
                  : mode === 'signup'
                    ? 'Create account'
                    : 'Log in'}
              </button>
            </form>
            <p className="auth-switch">
              {mode === 'signup' ? (
                <>
                  Already have an account?{' '}
                  <button type="button" onClick={() => setMode('signin')}>
                    Log in
                  </button>
                </>
              ) : (
                <>
                  New here?{' '}
                  <button type="button" onClick={() => setMode('signup')}>
                    Create an account
                  </button>
                </>
              )}
            </p>
          </>
        )}
      </div>
    </div>
  )
}
