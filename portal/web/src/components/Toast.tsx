import React, { useEffect } from 'react'
import { X, CheckCircle, AlertCircle, Info } from 'lucide-react'

export interface ToastProps {
  message: string
  type: 'success' | 'error' | 'info'
  onClose: () => void
  duration?: number
}

export default function Toast({ message, type, onClose, duration = 5000 }: ToastProps) {
  useEffect(() => {
    const timer = setTimeout(() => {
      onClose()
    }, duration)

    return () => clearTimeout(timer)
  }, [duration, onClose])

  const getIcon = () => {
    switch (type) {
      case 'success':
        return <CheckCircle className="h-5 w-5 text-green-500" />
      case 'error':
        return <AlertCircle className="h-5 w-5 text-red-500" />
      case 'info':
        return <Info className="h-5 w-5 text-blue-500" />
    }
  }

  const getStyles = () => {
    switch (type) {
      case 'success':
        return 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-800 dark:text-green-200'
      case 'error':
        return 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-800 dark:text-red-200'
      case 'info':
        return 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800 text-blue-800 dark:text-blue-200'
    }
  }

  return (
    // No fixed positioning here — the ToastProvider's wrapper owns
    // that (mobile-safe on <sm, top-right pill on sm+). Re-positioning
    // it here broke the mobile wrapper's overrides. Wilson UAT
    // 2026-08-08: long messages (import-error sha256 hashes) also
    // overflowed the pill; `break-all` + `min-w-0` on the text
    // element lets them wrap.
    <div className={`flex items-start gap-3 p-4 rounded-lg border shadow-lg w-full sm:max-w-md animate-in slide-in-from-right-full ${getStyles()}`}>
      <span className="flex-shrink-0 mt-0.5">{getIcon()}</span>
      <p className="flex-1 text-sm font-medium break-all min-w-0">{message}</p>
      <button
        onClick={onClose}
        className="flex-shrink-0 p-1 rounded-md hover:bg-black/10 dark:hover:bg-white/10 transition-colors"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
