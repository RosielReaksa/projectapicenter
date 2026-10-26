import { createContext, useCallback, useContext, useMemo, useState } from 'react'

const ToastContext = createContext(null)

let nextId = 1

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (message, tone = 'neutral', ttl = 4000) => {
      const id = nextId++
      setToasts((current) => [...current, { id, message, tone }])
      if (ttl) window.setTimeout(() => dismiss(id), ttl)
      return id
    },
    [dismiss],
  )

  const value = useMemo(
    () => ({
      push,
      success: (message) => push(message, 'success'),
      error: (message) => push(message, 'danger', 6000),
      info: (message) => push(message, 'neutral'),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm"
        role="status"
        aria-live="polite"
      >
        {toasts.map((toast) => (
          <button
            key={toast.id}
            onClick={() => dismiss(toast.id)}
            className={`animate-in text-left px-3 py-2.5 rounded-card border text-sm shadow-lg bg-bg
              ${
                toast.tone === 'success'
                  ? 'border-success/40 text-fg'
                  : toast.tone === 'danger'
                    ? 'border-danger/50 text-fg'
                    : 'border-border-strong text-fg'
              }`}
          >
            {toast.message}
          </button>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside ToastProvider')
  return context
}
