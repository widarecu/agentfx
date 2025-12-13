import { createBrowserRouter } from 'react-router-dom'
import App from './App'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import MarketData from './pages/MarketData'
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
    ],
  },
], {
  future: {
    v7_normalizeFormMethod: true,
    v7_partialHydration: true,
  },
})
