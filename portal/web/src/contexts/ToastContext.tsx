import React, { createContext, useContext, useReducer, ReactNode } from 'react'
import Toast, { ToastProps } from '../components/Toast'

interface ToastState {
  toasts: (ToastProps & { id: string })[]
}

interface ToastContextType {
  showToast: (message: string, type: 'success' | 'error' | 'info', duration?: number) => void
}

const ToastContext = createContext<ToastContextType | undefined>(undefined)

type ToastAction =
  | { type: 'ADD_TOAST'; payload: ToastProps & { id: string } }
  | { type: 'REMOVE_TOAST'; payload: string }

function toastReducer(state: ToastState, action: ToastAction): ToastState {
  switch (action.type) {
    case 'ADD_TOAST':
      return {
        ...state,
        toasts: [...state.toasts, action.payload]
      }
    case 'REMOVE_TOAST':
      return {
        ...state,
        toasts: state.toasts.filter(toast => toast.id !== action.payload)
      }
    default:
      return state
  }
}

export function useToast() {
  const context = useContext(ToastContext)
  if (context === undefined) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return context
}

interface ToastProviderProps {
  children: ReactNode
}

export function ToastProvider({ children }: ToastProviderProps) {
  const [state, dispatch] = useReducer(toastReducer, { toasts: [] })

  const showToast = (message: string, type: 'success' | 'error' | 'info', duration?: number) => {
    const id = Date.now().toString()
    dispatch({
      type: 'ADD_TOAST',
      payload: { message, type, onClose: () => removeToast(id), duration, id }
    })
  }

  const removeToast = (id: string) => {
    dispatch({ type: 'REMOVE_TOAST', payload: id })
  }

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="fixed top-4 right-4 z-50 space-y-2">
        {state.toasts.map(toast => (
          <Toast key={toast.id} {...toast} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}
