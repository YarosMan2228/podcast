import { Routes, Route } from 'react-router-dom'
import LandingPage from './pages/LandingPage.jsx'
import JobPage from './pages/JobPage.jsx'
import HistoryPage from './pages/HistoryPage.jsx'
import Toaster from './components/Toaster.jsx'
import AccessGate from './components/AccessGate.jsx'

// Under Vitest the pages run against mocks with no backend; skip the gate
// probe so existing page tests don't need a fetch stub.
const GATED = import.meta.env.MODE !== 'test'

export default function App() {
  const routes = (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/jobs/:jobId" element={<JobPage />} />
    </Routes>
  )
  return (
    <>
      {GATED ? <AccessGate>{routes}</AccessGate> : routes}
      <Toaster />
    </>
  )
}
