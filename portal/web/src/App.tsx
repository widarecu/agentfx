import React, { useEffect, useState } from 'react'
import { Outlet, Link, useLocation } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import { User, LogOut, Menu, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

const NAV_ITEMS: Array<{ to: string; label: string }> = [
  { to: '/', label: 'Dashboard' },
  { to: '/market-data', label: 'Market Data' },
  { to: '/optimization', label: 'Optimization' },
  { to: '/evolution', label: 'Evolution' },
  { to: '/paper', label: 'Paper' },
  { to: '/settings', label: 'Settings' },
]

/**
 * Paths where the App header + toast overlay hide themselves on
 * mobile so the page can own the whole viewport. The mobile
 * paper-run view has its own sticky status bar; stacking the
 * portal header on top would eat 56px + duplicate context.
 */
function isMobileFullScreenPath(pathname: string): boolean {
  return /^\/paper\/[^/]+/.test(pathname);
}

export default function App() {
  const { user, logout, isAuthenticated } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  const [theme, setTheme] = React.useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem('theme') as 'light' | 'dark' | null
    if (saved) return saved
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    return prefersDark ? 'dark' : 'light'
  })

  // Close the mobile menu on every route change so navigating a link
  // doesn't leave the drawer open behind the new page.
  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])

  const hideChromeOnMobile = isMobileFullScreenPath(location.pathname)

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
      <header
        className={`sticky top-0 z-30 bg-white/80 backdrop-blur border-b border-slate-200 dark:bg-slate-900/80 dark:border-slate-800 ${hideChromeOnMobile ? 'hidden sm:block' : ''}`}
      >
        <div className="mx-auto max-w-6xl px-4 py-3">
          {/* Top row — always visible. Compact on mobile: brand + user +
              hamburger. On sm+ inflates into the full desktop nav row. */}
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-6 min-w-0">
              <div className="font-semibold truncate">AgentFX Portal</div>
              <nav className="hidden sm:flex items-center gap-4">
                {NAV_ITEMS.map((item) => (
                  <Link
                    key={item.to}
                    to={item.to}
                    className="text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
              {user && (
                <div className="hidden md:flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400 min-w-0">
                  <User className="h-4 w-4 shrink-0" />
                  <span className="truncate">{user.full_name || user.email}</span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 text-sm">
              <button
                onClick={() => setTheme((t: 'light' | 'dark') => (t === 'dark' ? 'light' : 'dark'))}
                className="hidden sm:inline-flex rounded-md border border-slate-300 px-3 py-1 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                aria-label="Toggle dark mode"
              >
                {theme === 'dark' ? 'Light' : 'Dark'}
              </button>
              <button
                onClick={onLogout}
                className="hidden sm:inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-1 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
              >
                <LogOut className="h-4 w-4" />
                Logout
              </button>
              <button
                type="button"
                onClick={() => setMenuOpen((v) => !v)}
                className="sm:hidden inline-flex items-center justify-center rounded-md border border-slate-300 dark:border-slate-700 w-9 h-9 text-slate-700 dark:text-slate-200"
                aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                aria-expanded={menuOpen}
              >
                {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
              </button>
            </div>
          </div>

          {/* Mobile drop-down. Same content as the desktop nav plus
              theme toggle + logout so the sm-hidden buttons stay
              reachable. Closes automatically on route change. */}
          {menuOpen && (
            <div className="sm:hidden mt-3 flex flex-col gap-1 border-t border-slate-200 dark:border-slate-800 pt-3">
              {NAV_ITEMS.map((item) => (
                <Link
                  key={item.to}
                  to={item.to}
                  className="px-2 py-2 rounded text-sm text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  {item.label}
                </Link>
              ))}
              {user && (
                <div className="mt-1 px-2 py-2 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400 border-t border-slate-200 dark:border-slate-800">
                  <User className="h-4 w-4 shrink-0" />
                  <span className="truncate">{user.full_name || user.email}</span>
                </div>
              )}
              <div className="flex gap-2 mt-1">
                <button
                  onClick={() => setTheme((t: 'light' | 'dark') => (t === 'dark' ? 'light' : 'dark'))}
                  className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                  aria-label="Toggle dark mode"
                >
                  {theme === 'dark' ? 'Light' : 'Dark'}
                </button>
                <button
                  onClick={onLogout}
                  className="flex-1 inline-flex items-center justify-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                >
                  <LogOut className="h-4 w-4" />
                  Logout
                </button>
              </div>
            </div>
          )}
        </div>
      </header>
      <main
        className={
          hideChromeOnMobile
            ? 'sm:mx-auto sm:max-w-6xl sm:px-4 sm:py-6'
            : 'mx-auto max-w-6xl px-4 py-6'
        }
      >
        <Outlet />
      </main>
    </div>
  )
}
