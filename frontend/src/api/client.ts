import { clearLocal, current } from '../auth/session'

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string) {
    super(code)
    this.status = status
    this.code = code
  }
}

/** JSON request with the bearer session. A 401 ends the local session and returns to the login page. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const session = current()
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
        ...init.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'network')
  }

  if (response.status === 204) return undefined as T

  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    /* non-JSON body */
  }

  if (!response.ok) {
    if (response.status === 401) {
      clearLocal()
      window.location.assign('/login')
    }
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new ApiError(response.status, typeof detail === 'string' ? detail : 'unknown')
  }
  return body as T
}
