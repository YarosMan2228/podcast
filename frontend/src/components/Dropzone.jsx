import { useState, useRef } from 'react'

const ACCEPTED_MIME = /^(audio\/|video\/|application\/ogg)/

export default function Dropzone({ onFile }) {
  const [state, setState] = useState('idle') // idle | dragover | error
  const [errorMsg, setErrorMsg] = useState('')
  const inputRef = useRef(null)

  function validate(file) {
    if (!file) return 'No file selected.'
    if (!ACCEPTED_MIME.test(file.type)) return `Unsupported format: ${file.type || 'unknown'}`
    if (file.size > 500 * 1024 * 1024) return 'File exceeds 500 MB limit.'
    return null
  }

  function handleFile(file) {
    const err = validate(file)
    if (err) {
      setErrorMsg(err)
      setState('error')
      return
    }
    setState('idle')
    setErrorMsg('')
    onFile?.(file)
  }

  function onDrop(e) {
    e.preventDefault()
    setState('idle')
    handleFile(e.dataTransfer.files[0])
  }

  function onDragOver(e) {
    e.preventDefault()
    setState('dragover')
  }

  function onDragLeave() {
    setState('idle')
  }

  function onInputChange(e) {
    handleFile(e.target.files[0])
    e.target.value = ''
  }

  const borderColor =
    state === 'dragover' ? 'border-indigo-500 bg-indigo-50' :
    state === 'error'    ? 'border-red-400 bg-red-50' :
                           'border-gray-300 bg-white hover:border-indigo-400'

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="Upload audio or video file"
      className={`w-full max-w-xl border-2 border-dashed rounded-2xl p-14 flex flex-col items-center gap-4 cursor-pointer transition-colors ${borderColor}`}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept="audio/*,video/*,application/ogg"
        className="hidden"
        onChange={onInputChange}
        data-testid="file-input"
      />

      {state === 'dragover' && (
        <p className="text-indigo-600 font-semibold text-lg">Drop it!</p>
      )}

      {state === 'error' && (
        <p className="text-red-500 font-medium text-sm text-center" role="alert">{errorMsg}</p>
      )}

      {state !== 'dragover' && (
        <>
          <svg className="w-12 h-12 text-gray-300" fill="none" viewBox="0 0 48 48" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M24 8v24m0-24l-8 8m8-8l8 8M8 36h32" />
          </svg>
          <p className="text-gray-500 text-sm text-center">
            Drag &amp; drop your podcast file here, or <span className="text-indigo-600 underline">browse</span>
          </p>
          <p className="text-gray-400 text-xs">MP3, MP4, WAV, OGG · max 500 MB</p>
        </>
      )}
    </div>
  )
}
