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
 * Job options sent with every new episode (Pro).
 * @typedef {Object} JobOptions
 * @property {string} [podcast_name]
 * @property {string} [brand_color]  #RRGGBB
 * @property {File|null} [logo]
 */

function appendOptions(form, options = {}) {
  if (options.podcast_name) form.append('podcast_name', options.podcast_name)
  if (options.brand_color) form.append('brand_color', options.brand_color)
  if (options.logo) form.append('logo', options.logo)
  if (options.clip_layout) form.append('clip_layout', options.clip_layout)
  if (options.caption_style) form.append('caption_style', options.caption_style)
}

/**
 * Upload a file to /api/jobs/upload.
 * @param {File} file
 * @param {JobOptions} [options]
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function uploadFile(file, options = {}) {
  const form = new FormData()
  form.append('file', file)
  appendOptions(form, options)
  const res = await fetch('/api/jobs/upload', { method: 'POST', body: form })
  return handleResponse(res)
}

/**
 * Submit a YouTube URL to /api/jobs/from_url.
 * Sends JSON unless a logo file is attached (then multipart).
 * @param {string} url
 * @param {JobOptions} [options]
 * @returns {Promise<{job_id: string, status: string}>}
 */
export async function submitUrl(url, options = {}) {
  let res
  if (options.logo) {
    const form = new FormData()
    form.append('url', url)
    appendOptions(form, options)
    res = await fetch('/api/jobs/from_url', { method: 'POST', body: form })
  } else {
    const body = { url }
    if (options.podcast_name) body.podcast_name = options.podcast_name
    if (options.brand_color) body.brand_color = options.brand_color
    if (options.clip_layout) body.clip_layout = options.clip_layout
    if (options.caption_style) body.caption_style = options.caption_style
    res = await fetch('/api/jobs/from_url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  }
  return handleResponse(res)
}

/**
 * List recent jobs (newest first) for the history page.
 * @param {number} limit
 * @returns {Promise<{jobs: object[]}>}
 */
export async function listJobs(limit = 50) {
  const res = await fetch(`/api/jobs?limit=${limit}`)
  return handleResponse(res)
}

/**
 * Delete a job and every file it produced.
 * @param {string} jobId
 * @returns {Promise<{deleted: boolean, job_id: string}>}
 */
export async function deleteJob(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' })
  return handleResponse(res)
}

/**
 * Regenerate an artifact, optionally with a tone override and/or a free-text
 * hint ("shorter", "focus on the pricing part").
 * @param {string} artifactId
 * @param {string|null} tone
 * @param {string|null} [hint]
 * @returns {Promise<{artifact_id: string, status: string, version: number}>}
 */
export async function regenerateArtifact(artifactId, tone, hint = null) {
  const body = {}
  if (tone) body.tone = tone
  if (hint && hint.trim()) body.hint = hint.trim()
  const res = await fetch(`/api/artifacts/${artifactId}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}
