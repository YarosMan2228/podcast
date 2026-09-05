/**
 * Branding defaults + localStorage persistence (Pro).
 * Kept out of the component file so React Fast Refresh stays happy.
 */
const STORAGE_KEY = 'podcastpack.branding'

export const DEFAULT_BRANDING = { podcast_name: '', brand_color: '#6366f1' }

/** Persisted name + colour (never the logo File) so the next upload is one click. */
export function loadStoredBranding() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_BRANDING
    const parsed = JSON.parse(raw)
    return {
      podcast_name: typeof parsed.podcast_name === 'string' ? parsed.podcast_name : '',
      brand_color: /^#[0-9a-fA-F]{6}$/.test(parsed.brand_color ?? '')
        ? parsed.brand_color
        : DEFAULT_BRANDING.brand_color,
    }
  } catch {
    return DEFAULT_BRANDING
  }
}

export function storeBranding(value) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      podcast_name: value.podcast_name,
      brand_color: value.brand_color,
    }))
  } catch {
    // private mode etc. — remembering is a convenience, not a requirement
  }
}
