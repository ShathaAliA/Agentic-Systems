import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/marhey/arabic-700.css'
import '@fontsource/marhey/latin-700.css'
import '@fontsource/readex-pro/arabic-400.css'
import '@fontsource/readex-pro/arabic-500.css'
import '@fontsource/readex-pro/arabic-600.css'
import '@fontsource/readex-pro/arabic-700.css'
import '@fontsource/readex-pro/latin-400.css'
import '@fontsource/readex-pro/latin-600.css'
import '@fontsource/readex-pro/latin-700.css'
import './styles/tokens.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
