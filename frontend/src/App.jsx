import { Routes, Route } from 'react-router-dom'
import LandingPage from './pages/LandingPage.jsx'
import JobPage from './pages/JobPage.jsx'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/jobs/:jobId" element={<JobPage />} />
    </Routes>
  )
}
