import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './app/App';
import { setOnUnauthorized, setTokenGetter } from './services/apiClient';
import { logoutSession } from './services/session';
import { useAuthStore } from './stores/authStore';
import './styles/app.css';

// Session expiry / auth failure: clear the session (auth + session-local
// download state) and bounce to the login page. The full reload resets any
// half-open sockets/stores cleanly; logoutSession covers the pre-reload ticks.
setOnUnauthorized(() => {
  logoutSession();
  if (window.location.pathname !== '/login') {
    window.location.assign('/login');
  }
});

// Wire the bearer-token source into the API client (keeps the dependency
// direction one-way: apiClient ← api ← authStore).
setTokenGetter(() => useAuthStore.getState().token);

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Missing #root element — index.html and main.tsx disagree.');
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
