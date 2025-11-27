import React from 'react'
import { Link } from 'react-router-dom'

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <div className="border-b border-slate-200 pb-5 dark:border-slate-800">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          Welcome to AgentFX Portal
        </h1>
        <p className="mt-2 text-slate-600 dark:text-slate-400">
          Your authentication is working! This dashboard will display trading runs and analytics once the backend endpoints are implemented.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <div className="bg-white p-6 rounded-lg border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
          <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-2">
            🎯 Authentication Status
          </h3>
          <p className="text-green-600 dark:text-green-400">
            ✅ Successfully authenticated
          </p>
          <p className="text-sm text-slate-600 dark:text-slate-400 mt-2">
            JWT token is active and working properly.
          </p>
        </div>

        <div className="bg-white p-6 rounded-lg border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
          <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-2">
            📊 Trading Runs
          </h3>
          <p className="text-slate-600 dark:text-slate-400">
            Coming soon...
          </p>
          <p className="text-sm text-slate-500 dark:text-slate-500 mt-2">
            Backend endpoints for trading data will be implemented next.
          </p>
        </div>

        <div className="bg-white p-6 rounded-lg border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
          <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-2">
            🔧 System Status
          </h3>
          <p className="text-green-600 dark:text-green-400">
            ✅ Frontend: Operational
          </p>
          <p className="text-green-600 dark:text-green-400">
            ✅ Backend: Operational
          </p>
          <p className="text-green-600 dark:text-green-400">
            ✅ Authentication: Working
          </p>
        </div>
      </div>

      <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
        <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-4">
          🚀 Next Steps
        </h3>
        <ul className="space-y-2 text-slate-600 dark:text-slate-400">
          <li>• Implement trading run management endpoints</li>
          <li>• Add real-time market data integration</li>
          <li>• Create strategy configuration interface</li>
          <li>• Build performance analytics dashboard</li>
        </ul>
      </div>

      <div className="flex justify-center">
        <Link
          to="/login"
          className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 bg-white hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
        >
          Test Logout Flow
        </Link>
      </div>
    </div>
  )
}
