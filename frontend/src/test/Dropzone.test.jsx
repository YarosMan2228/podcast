import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'
import Dropzone from '../components/Dropzone.jsx'

function mkFile(name, type, sizeBytes = 1024) {
  const file = new File(['x'.repeat(sizeBytes)], name, { type })
  return file
}

function renderDropzone(onFile = vi.fn()) {
  render(<Dropzone onFile={onFile} />)
  return { zone: screen.getByRole('button'), onFile }
}

describe('Dropzone — idle state', () => {
  test('renders upload hint text', () => {
    renderDropzone()
    expect(screen.getByText(/drag & drop/i)).toBeInTheDocument()
  })

  test('renders accepted formats note', () => {
    renderDropzone()
    expect(screen.getByText(/MP3.*MP4.*WAV/i)).toBeInTheDocument()
  })
})

describe('Dropzone — dragover state', () => {
  test('shows "Drop it!" on dragover', () => {
    const { zone } = renderDropzone()
    fireEvent.dragOver(zone)
    expect(screen.getByText(/drop it/i)).toBeInTheDocument()
  })

  test('reverts to idle on dragleave', () => {
    const { zone } = renderDropzone()
    fireEvent.dragOver(zone)
    fireEvent.dragLeave(zone)
    expect(screen.queryByText(/drop it/i)).not.toBeInTheDocument()
  })
})

describe('Dropzone — valid file via drop', () => {
  test('calls onFile with valid audio file', () => {
    const { zone, onFile } = renderDropzone()
    const file = mkFile('ep.mp3', 'audio/mpeg')
    fireEvent.drop(zone, { dataTransfer: { files: [file] } })
    expect(onFile).toHaveBeenCalledWith(file)
  })

  test('calls onFile with valid video file', () => {
    const { zone, onFile } = renderDropzone()
    const file = mkFile('ep.mp4', 'video/mp4')
    fireEvent.drop(zone, { dataTransfer: { files: [file] } })
    expect(onFile).toHaveBeenCalledWith(file)
  })
})

describe('Dropzone — invalid file', () => {
  test('shows error and does NOT call onFile for wrong mime', () => {
    const { zone, onFile } = renderDropzone()
    const file = mkFile('doc.pdf', 'application/pdf')
    fireEvent.drop(zone, { dataTransfer: { files: [file] } })
    expect(onFile).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/unsupported format/i)
  })

  test('shows error for file over 500 MB', () => {
    const { zone, onFile } = renderDropzone()
    const bigFile = mkFile('huge.mp3', 'audio/mpeg', 501 * 1024 * 1024)
    fireEvent.drop(zone, { dataTransfer: { files: [bigFile] } })
    expect(onFile).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/500 mb/i)
  })
})

describe('Dropzone — file input (browse)', () => {
  test('calls onFile when file chosen via input', () => {
    const onFile = vi.fn()
    render(<Dropzone onFile={onFile} />)
    const input = screen.getByTestId('file-input')
    const file = mkFile('ep.wav', 'audio/wav')
    fireEvent.change(input, { target: { files: [file] } })
    expect(onFile).toHaveBeenCalledWith(file)
  })
})
