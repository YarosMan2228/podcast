import Dropzone from '../components/Dropzone.jsx'

export default function LandingPage() {
  function handleFile(file) {
    // TODO Day 4: POST /api/jobs/upload
    console.log('File selected:', file.name, file.size)
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-8 px-4">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-indigo-600 mb-2">Podcast → Full Content Pack</h1>
        <p className="text-gray-500 text-lg">Upload your episode and get 10+ ready-to-publish assets in minutes.</p>
      </div>

      <Dropzone onFile={handleFile} />

      <div className="flex gap-12 text-center text-sm text-gray-500 mt-4">
        <div><span className="block text-2xl mb-1">🎙️</span>Upload</div>
        <div><span className="block text-2xl mb-1">⚡</span>Process</div>
        <div><span className="block text-2xl mb-1">📦</span>Download Pack</div>
      </div>
    </div>
  )
}
