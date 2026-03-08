/*
Entry Point

Mounts the React app into the DOM. StrictMode enables
extra development warnings for detecting potential issues.
*/

import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css' // Tailwind CSS v4 + custom styles

// Render the app into the #root div in index.html
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
