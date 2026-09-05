import { useEffect, useMemo, useState } from 'react'
import { storeBranding, CLIP_LAYOUTS, CAPTION_STYLES } from '../api/branding.js'

const LOGO_MIMES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'])
const MAX_LOGO_BYTES = 2 * 1024 * 1024

/**
 * Collapsible "Branding" panel for the landing page.
 * `value` = { podcast_name, brand_color, logo: File | null }
 */
export default function BrandingSettings({ value, onChange }) {
  const [open, setOpen] = useState(Boolean(value.podcast_name))
  const [logoError, setLogoError] = useState('')

  // Derived, not state: one object URL per logo File, revoked when it changes.
  const logoPreview = useMemo(
    () => (value.logo ? URL.createObjectURL(value.logo) : null),
    [value.logo],
  )
  useEffect(() => {
    if (!logoPreview) return undefined
    return () => URL.revokeObjectURL(logoPreview)
  }, [logoPreview])

  function update(patch) {
    const next = { ...value, ...patch }
    storeBranding(next)
    onChange(next)
  }

  function onLogo(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (!LOGO_MIMES.has(file.type)) {
      setLogoError('Logo must be PNG, JPEG, WebP or SVG')
      return
    }
    if (file.size > MAX_LOGO_BYTES) {
      setLogoError('Logo must be under 2 MB')
      return
    }
    setLogoError('')
    update({ logo: file })
  }

  return (
    <section className="w-full max-w-xl" aria-label="Branding settings">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-sm text-gray-500 hover:text-indigo-600 flex items-center gap-2"
      >
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
        Branding &amp; clip options
        {(value.podcast_name || value.logo) && !open && (
          <span className="text-xs text-gray-400">
            · {value.podcast_name || 'logo set'}
          </span>
        )}
      </button>

      {open && (
        <div className="mt-3 bg-white border border-gray-200 rounded-2xl p-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">Podcast name</span>
            <input
              type="text"
              maxLength={120}
              value={value.podcast_name}
              onChange={(e) => update({ podcast_name: e.target.value })}
              placeholder="Shown on graphics and the thumbnail"
              className="border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              aria-label="Podcast name"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">Accent colour</span>
            <span className="flex items-center gap-2">
              <input
                type="color"
                value={value.brand_color}
                onChange={(e) => update({ brand_color: e.target.value })}
                aria-label="Accent colour"
                className="h-9 w-12 border border-gray-300 rounded cursor-pointer bg-white"
              />
              <code className="text-xs text-gray-500">{value.brand_color}</code>
            </span>
          </label>

          <div className="sm:col-span-2 flex items-center gap-3 text-sm">
            <label className="flex-1 flex flex-col gap-1">
              <span className="text-gray-600">Logo (PNG/JPEG/WebP/SVG, ≤ 2 MB)</span>
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp,image/svg+xml"
                onChange={onLogo}
                aria-label="Logo file"
                className="text-xs text-gray-500"
              />
              {logoError && <span role="alert" className="text-xs text-red-500">{logoError}</span>}
            </label>
            {logoPreview && (
              <span className="flex items-center gap-2">
                <img src={logoPreview} alt="Logo preview" className="h-10 max-w-[120px] object-contain" />
                <button
                  type="button"
                  onClick={() => update({ logo: null })}
                  className="text-xs text-gray-400 hover:text-red-500"
                  aria-label="Remove logo"
                >
                  remove
                </button>
              </span>
            )}
          </div>

          <fieldset className="sm:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-4 border-t border-gray-100 pt-4">
            <legend className="sr-only">Clip options</legend>

            <div className="flex flex-col gap-1 text-sm" role="radiogroup" aria-label="Vertical layout">
              <span className="text-gray-600">Vertical clips (9:16)</span>
              {CLIP_LAYOUTS.map((opt) => (
                <label key={opt.value} className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="clip_layout"
                    value={opt.value}
                    checked={value.clip_layout === opt.value}
                    onChange={() => update({ clip_layout: opt.value })}
                    className="mt-1"
                  />
                  <span>
                    <span className="text-gray-800">{opt.label}</span>
                    <span className="block text-xs text-gray-400">{opt.hint}</span>
                  </span>
                </label>
              ))}
            </div>

            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">Captions</span>
              <select
                value={value.caption_style}
                onChange={(e) => update({ caption_style: e.target.value })}
                aria-label="Caption style"
                className="border border-gray-300 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                {CAPTION_STYLES.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <span className="text-xs text-gray-400">
                {CAPTION_STYLES.find((o) => o.value === value.caption_style)?.hint}
              </span>
            </label>
          </fieldset>
        </div>
      )}
    </section>
  )
}
