import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initAnonMode } from './utils/anonymize'

if ('scrollRestoration' in history) {
  history.scrollRestoration = 'manual';
}

// Screenshot mode: no-op unless the hidden keyword flipped it on (see AppShell).
initAnonMode();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
