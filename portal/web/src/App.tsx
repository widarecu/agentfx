import React, { useEffect } from 'react'
import { Outlet, Link } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import { User, LogOut } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

export default function App() {
  const { user, logout, isAuthenticated } = useAuth()
  const navigate = useNavigate()
  const [theme, setTheme] = React.useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('theme') as 'light' | 'dark' | null
    if (saved) return saved
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    return prefersDark ? 'dark' : 'light'
  })

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') root.classList.add('dark')
    else root.classList.remove('dark')
    localStorage.setItem('theme', theme)
  }, [theme])

  const onLogout = async () => {
    await logout()
    navigate('/login')
  }

  if (!isAuthenticated) {
    return null // Will be handled by ProtectedRoute
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-900 dark:text-slate-100">
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur border-b border-slate-200 dark:bg-slate-900/80 dark:border-slate-800">
        <div className="mx-auto max-w-6xl px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-6">
              <div className="font-semibold">AgentFX Portal</div>
              <nav className="flex items-center gap-4">
                <Link 
                  to="/" 
                  className="text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
                >
                  Dashboard
                </Link>
                <Link 
                  to="/market-data" 
                  className="text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
                >
                  Market Data
                </Link>
              </nav>
              {user && (
                <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
                  <User className="h-4 w-4" />
                  <span>{user.full_name || user.email}</span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 text-sm">
              <button
                onClick={() => setTheme((t: 'light' | 'dark') => (t === 'dark' ? 'light' : 'dark'))}
                className="rounded-md border border-slate-300 px-3 py-1 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                aria-label="Toggle dark mode"
              >
                {theme === 'dark' ? 'Light' : 'Dark'}
              </button>
              <button 
                onClick={onLogout} 
                className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-1 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
              >
                <LogOut className="h-4 w-4" />
                Logout
              </button>
            </div>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
