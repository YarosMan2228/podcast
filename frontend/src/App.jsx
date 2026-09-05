import { Routes, Route } from 'react-router-dom'
import LandingPage from './pages/LandingPage.jsx'
import JobPage from './pages/JobPage.jsx'
import HistoryPage from './pages/HistoryPage.jsx'
import Toaster from './components/Toaster.jsx'

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/jobs/:jobId" element={<JobPage />} />
      </Routes>
      <Toaster />
    </>
  )
}
