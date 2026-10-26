import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

// Self-hosted fonts (STYLE_UPDATE.md §4): no third-party CDN requests.
// Only the weights and subsets the interface actually uses.
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/newsreader/400.css'
import '@fontsource/newsreader/500.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'

import App from './App'
import { AuthProvider } from './lib/auth'
import { SiteProvider } from './lib/site'
import { ToastProvider } from './lib/toast'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <SiteProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </SiteProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
