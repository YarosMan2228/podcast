/**
 * Branding defaults + localStorage persistence (Pro).
 * Kept out of the component file so React Fast Refresh stays happy.
 */
const STORAGE_KEY = 'podcastpack.branding'

export const CLIP_LAYOUTS = [
  { value: 'pad',  label: 'Fit (black bars)',   hint: 'Whole frame kept, bars top and bottom' },
  { value: 'crop', label: 'Fill (center crop)', hint: 'Fills 9:16, cuts the sides — best for talking heads' },
]

export const CAPTION_STYLES = [
  { value: 'karaoke', label: 'Karaoke', hint: 'Word-by-word highlight in your accent colour' },
  { value: 'clean',   label: 'Clean',   hint: 'Plain white text, thick outline' },
  { value: 'boxed',   label: 'Boxed',   hint: 'White on a dark box, accent highlight' },
]

export const DEFAULT_BRANDING = {
  podcast_name: '',
  brand_color: '#6366f1',
  clip_layout: 'pad',
  caption_style: 'karaoke',
}

function oneOf(list, value, fallback) {
  return list.some((o) => o.value === value) ? value : fallback
}

/** Persisted name / colour / clip options (never the logo File). */
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
      clip_layout: oneOf(CLIP_LAYOUTS, parsed.clip_layout, DEFAULT_BRANDING.clip_layout),
      caption_style: oneOf(CAPTION_STYLES, parsed.caption_style, DEFAULT_BRANDING.caption_style),
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
      clip_layout: value.clip_layout,
      caption_style: value.caption_style,
    }))
  } catch {
    // private mode etc. — remembering is a convenience, not a requirement
  }
}
