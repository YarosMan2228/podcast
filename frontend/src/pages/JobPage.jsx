import { useParams } from 'react-router-dom'

export default function JobPage() {
  const { jobId } = useParams()

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-4 px-4">
      <h2 className="text-2xl font-semibold text-gray-800">Job: {jobId}</h2>
      <p className="text-gray-500">Progress / Results (coming next)</p>
    </div>
  )
}
