import Dropzone from '../components/Dropzone.jsx'
import UrlInput from '../components/UrlInput.jsx'

export default function LandingPage() {
  function handleFile(file) {
    // TODO Day 4: POST /api/jobs/upload → navigate to /jobs/:id
    console.log('File selected:', file.name, file.size)
  }

  function handleUrl(url) {
    // TODO Day 4: POST /api/jobs/from_url → navigate to /jobs/:id
    console.log('URL submitted:', url)
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-8 px-4">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-indigo-600 mb-2">Podcast → Full Content Pack</h1>
        <p className="text-gray-500 text-lg">
          Upload your episode and get 10+ ready-to-publish assets in minutes.
        </p>
      </div>

      <Dropzone onFile={handleFile} />

      <div className="flex items-center gap-3 w-full max-w-xl text-sm text-gray-400">
        <div className="flex-1 h-px bg-gray-200" />
        <span>or paste a URL</span>
        <div className="flex-1 h-px bg-gray-200" />
      </div>

      <UrlInput onSubmit={handleUrl} />

      <div className="flex gap-12 text-center text-sm text-gray-500 mt-4">
        <div>
          <span className="block text-2xl mb-1" aria-hidden="true">1</span>
          Upload
        </div>
        <div>
          <span className="block text-2xl mb-1" aria-hidden="true">2</span>
          Process
        </div>
        <div>
          <span className="block text-2xl mb-1" aria-hidden="true">3</span>
          Download Pack
        </div>
      </div>
    </div>
  )
}
