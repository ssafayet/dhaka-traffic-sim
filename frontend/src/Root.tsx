import { Suspense, lazy, useEffect, useState } from 'react'

// The docs don't need the map, and the simulator doesn't need the docs.
const App = lazy(() => import('./App.tsx'))
const Docs = lazy(() => import('./components/Docs.tsx'))

const isDocs = () => location.hash.startsWith('#docs')

/** The simulator, or the docs at #docs. */
export function Root() {
  const [docs, setDocs] = useState(isDocs)
  useEffect(() => {
    const on = () => setDocs(isDocs())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return <Suspense fallback={null}>{docs ? <Docs /> : <App />}</Suspense>
}
