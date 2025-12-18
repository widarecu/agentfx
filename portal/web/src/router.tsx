import { createBrowserRouter } from 'react-router-dom'
import App from './App'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import MarketData from './pages/MarketData'
import Optimization from './pages/Optimization'
import OptimizationDetail from './pages/OptimizationDetail'
import TrialDetail from './pages/TrialDetail'
import { ProtectedRoute } from './components/ProtectedRoute'

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
    ],
  },
], {
  future: {
    v7_normalizeFormMethod: true,
    v7_partialHydration: true,
  },
})
