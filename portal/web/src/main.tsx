import React from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { AppRouter } from './components/AppRouter'

console.log('Starting AgentFX Portal...')

const rootElement = document.getElementById('root')
if (!rootElement) {
  console.error('Root element not found!')
  throw new Error('Root element not found')
}

// Clear any existing content and create new root
rootElement.innerHTML = ''
const root = createRoot(rootElement)

root.render(<AppRouter />)

console.log('AgentFX Portal rendered successfully!')
