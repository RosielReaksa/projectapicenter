/** API client: cookie auth, CSRF header, typed errors. */

export class ApiError extends Error {
  constructor(message, status, code, requestId, attempts) {
    super(message)
    this.status = status
    this.code = code
    this.requestId = requestId
    this.attempts = attempts || []
  }
}

function readCookie(name) {
  const match = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[2]) : ''
}

async function request(path, { method = 'GET', body, headers = {}, raw = false } = {}) {
  const options = {
    method,
    credentials: 'same-origin',
    headers: { Accept: 'application/json', ...headers },
  }

  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }

  if (method !== 'GET' && method !== 'HEAD') {
    options.headers['X-CSRF-Token'] = readCookie('gw_csrf')
  }

  let response
  try {
    response = await fetch(path, options)
  } catch {
    throw new ApiError('Cannot reach the gateway. Is it running?', 0, 'network_error')
  }

  if (response.status === 204) return null

  if (raw) {
    if (!response.ok) throw new ApiError('Request failed', response.status)
    return response.text()
  }

  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }

  if (!response.ok) {
    const error = data?.error || {}
    throw new ApiError(
      error.message || `Request failed (${response.status})`,
      response.status,
      error.code || 'error',
      error.request_id,
      error.attempts,
    )
  }

  return data
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: 'POST', body }),
  put: (path, body) => request(path, { method: 'PUT', body }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  del: (path) => request(path, { method: 'DELETE' }),
  text: (path) => request(path, { raw: true }),
}

/** Query-string builder that drops empty values. */
export function qs(params) {
  const search = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}
