import { useState, useEffect } from 'react'

const DURATION_MS = 3000

export default function Toaster() {
  const [toasts, setToasts] = useState([])

  useEffect(() => {
    function handler(e) {
      const id = Date.now() + Math.random()
      const { message, type } = e.detail
      setToasts((prev) => [...prev, { id, message, type }])
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id))
      }, DURATION_MS)
    }
    window.addEventListener('app:toast', handler)
    return () => window.removeEventListener('app:toast', handler)
  }, [])

  if (toasts.length === 0) return null

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 items-end pointer-events-none"
    >
      {toasts.map(({ id, message, type }) => (
        <div
          key={id}
          role="status"
          className={`px-4 py-2.5 rounded-xl text-sm font-medium shadow-lg pointer-events-auto transition-all animate-in slide-in-from-right duration-200 ${
            type === 'error'
              ? 'bg-red-600 text-white'
              : type === 'warning'
              ? 'bg-amber-500 text-white'
              : 'bg-gray-900 text-white'
          }`}
        >
          {message}
        </div>
      ))}
    </div>
  )
}
