/**
 * Thin fetch wrappers for the Podcast Pack API.
 *
 * All functions throw an Error with a human-readable `message` on non-2xx
 * responses. The error also carries `.code` (the API error code string) so
 * callers can branch on specific codes when needed.
 */

async function handleResponse(res) {
  if (res.ok) return res.json()
  let body
  try {
    body = await res.json()
  } catch {
    throw Object.assign(new Error(`HTTP ${res.status}`), { code: 'HTTP_ERROR' })
  }
  const err = body?.error ?? {}
  throw Object.assign(new Error(err.message ?? `HTTP ${res.status}`), {
    code: err.code ?? 'HTTP_ERROR',
    field: err.field,
  })
}

/**
 * Upload a file to /api/jobs/upload.
 * @param {File} file
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch('/api/jobs/upload', { method: 'POST', body: form })
  return handleResponse(res)
}

/**
 * Submit a YouTube URL to /api/jobs/from_url.
 * @param {string} url
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function submitUrl(url) {
  const res = await fetch('/api/jobs/from_url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  return handleResponse(res)
}

/**
 * Regenerate an artifact, optionally with a tone override.
 * @param {string} artifactId
 * @param {string|null} tone
 * @returns {Promise<{artifact_id: string, status: string, version: number}>}
 */
export async function regenerateArtifact(artifactId, tone) {
  const body = tone ? { tone } : {}
  const res = await fetch(`/api/artifacts/${artifactId}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}
