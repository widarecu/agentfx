import { createBrowserRouter } from 'react-router-dom'
import App from './App'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import MarketData from './pages/MarketData'
import Optimization from './pages/Optimization'
import OptimizationDetail from './pages/OptimizationDetail'
import TrialDetail from './pages/TrialDetail'
import EvolutionRuns from './pages/EvolutionRuns'
import EvolutionRunDetail from './pages/EvolutionRunDetail'
import GenomeDetail from './pages/GenomeDetail'
import GenomeBacktest from './pages/GenomeBacktest'
import ChartGallery from './pages/ChartGallery'
import Settings from './pages/Settings'
import PaperRuns from './pages/PaperRuns'
import ApprovalQueue from './pages/ApprovalQueue'
import { ProtectedRoute } from './components/ProtectedRoute'
import { RequireRole } from './components/RequireRole'

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <Login />,
  },
  {
    path: '/register',
    element: <Register />,
  },
  {
    path: '/',
    element: (
      <ProtectedRoute>
        <App />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'market-data', element: <MarketData /> },
      { path: 'optimization', element: <Optimization /> },
      { path: 'optimization/:jobId', element: <OptimizationDetail /> },
      { path: 'optimization/:jobId/trials/:trialId', element: <TrialDetail /> },
      // v0.3.0 — Autonomous Strategy Evolution
      { path: 'evolution', element: <EvolutionRuns /> },
      { path: 'evolution/:runId', element: <EvolutionRunDetail /> },
      { path: 'evolution/:runId/genomes/:genomeId', element: <GenomeDetail /> },
      { path: 'evolution/:runId/genomes/:genomeId/backtest', element: <GenomeBacktest /> },
      // v0.6.0 — settings + credentials. `RequireRole` composes with
      // the outer `ProtectedRoute` so VIEWER lands here → redirect
      // home with a toast (US-018 + PR-B).
      {
        path: 'settings',
        element: (
          <RequireRole allowed={['trader', 'admin']}>
            <Settings />
          </RequireRole>
        ),
      },
      // v0.6.0 — paper trading. All trader/admin only. PR-J adds
      // /paper/:id for the run detail page.
      {
        path: 'paper',
        element: (
          <RequireRole allowed={['trader', 'admin']}>
            <PaperRuns />
          </RequireRole>
        ),
      },
      {
        path: 'paper/approvals',
        element: (
          <RequireRole allowed={['trader', 'admin']}>
            <ApprovalQueue />
          </RequireRole>
        ),
      },
      // Dev-only verification gallery — renders every canonical seed
      // strategy on the same candle window so every indicator's chart
      // routing / tooltip / archetype can be visually swept at once.
      // Not linked from the nav; reach via /dev/chart-gallery.
      { path: 'dev/chart-gallery', element: <ChartGallery /> },
    ],
  },
], {
  future: {
    v7_normalizeFormMethod: true,
    v7_partialHydration: true,
  },
})
