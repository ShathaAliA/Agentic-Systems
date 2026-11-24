// Development password login against POST /auth/dev/login (Backend/docs/authentication.md).
// The session is an opaque bearer token with an absolute expiry and no refresh, so it is kept
// in sessionStorage with its expiry time and dropped as soon as it has passed.

const KEY = 'ai-platform.session'

export interface Session {
  token: string
  expiresAt: number
}

export type LoginFailure = 'invalid' | 'rate-limited' | 'unavailable'

export type LoginResult =
  | { ok: true; session: Session }
  | { ok: false; reason: LoginFailure; retryAfter?: number }

export async function login(email: string, password: string): Promise<LoginResult> {
  let response: Response
  try {
    response = await fetch('/auth/dev/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
  } catch {
    return { ok: false, reason: 'unavailable' }
  }

  if (response.ok) {
    const body = (await response.json()) as { access_token: string; expires_in: number }
    const session = { token: body.access_token, expiresAt: Date.now() + body.expires_in * 1000 }
    save(session)
    return { ok: true, session }
  }
  if (response.status === 401) return { ok: false, reason: 'invalid' }
  if (response.status === 429) {
    const retry = Number(response.headers.get('Retry-After'))
    return { ok: false, reason: 'rate-limited', retryAfter: Number.isFinite(retry) && retry > 0 ? retry : undefined }
  }
  return { ok: false, reason: 'unavailable' }
}

export function save(session: Session) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    /* storage blocked: the session then lasts only until reload */
  }
}

export function current(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const session = JSON.parse(raw) as Session
    if (session.expiresAt <= Date.now()) {
      sessionStorage.removeItem(KEY)
      return null
    }
    return session
  } catch {
    return null
  }
}

/** Drop the local session only; used when the server has already rejected it. */
export function clearLocal() {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* nothing stored */
  }
}

export async function logout() {
  const session = current()
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* nothing stored */
  }
  if (session) {
    try {
      await fetch('/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${session.token}` } })
    } catch {
      /* the server session still expires on its own */
    }
  }
}
