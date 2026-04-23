import { describe, test, expect } from 'vitest'
import {
  job_state_processing,
  job_state_completed,
  MOCK_SSE_SEQUENCE,
} from '../api/mocks.js'

// ---------------------------------------------------------------------------
// Constants from SPEC §1.2 / §1.3
// ---------------------------------------------------------------------------

const VALID_ARTIFACT_TYPES = new Set([
  'VIDEO_CLIP',
  'LINKEDIN_POST',
  'TWITTER_THREAD',
  'SHOW_NOTES',
  'NEWSLETTER',
  'YOUTUBE_DESCRIPTION',
  'QUOTE_GRAPHIC',
])

const VALID_ARTIFACT_STATUSES = new Set(['QUEUED', 'PROCESSING', 'READY', 'FAILED'])

const VALID_JOB_STATUSES = new Set([
  'PENDING', 'INGESTING', 'TRANSCRIBING', 'ANALYZING',
  'GENERATING', 'PACKAGING', 'COMPLETED', 'FAILED',
])

const VALID_SSE_EVENT_TYPES = new Set([
  'status_changed', 'artifact_ready', 'artifact_failed', 'completed',
])

// ---------------------------------------------------------------------------
// Shared artifact shape validator
// ---------------------------------------------------------------------------

function assertArtifactShape(artifact) {
  expect(artifact).toHaveProperty('id')
  expect(typeof artifact.id).toBe('string')
  expect(artifact.id.length).toBeGreaterThan(0)

  expect(VALID_ARTIFACT_TYPES).toContain(artifact.type)
  expect(VALID_ARTIFACT_STATUSES).toContain(artifact.status)

  expect(typeof artifact.index).toBe('number')
  expect(artifact.index).toBeGreaterThanOrEqual(0)

  expect(typeof artifact.version).toBe('number')
  expect(artifact.version).toBeGreaterThanOrEqual(1)

  // file_url и text_content могут быть null, но не undefined
  expect('file_url' in artifact).toBe(true)
  expect('text_content' in artifact).toBe(true)
  expect('metadata' in artifact).toBe(true)
}

// ---------------------------------------------------------------------------
// job_state_processing
// ---------------------------------------------------------------------------

describe('job_state_processing — top-level shape', () => {
  test('has all required top-level fields', () => {
    const j = job_state_processing
    expect(j).toHaveProperty('job_id')
    expect(j).toHaveProperty('status')
    expect(j).toHaveProperty('progress')
    expect(j).toHaveProperty('analysis')
    expect(j).toHaveProperty('artifacts')
    expect(j).toHaveProperty('package_url')
    expect(j).toHaveProperty('error')
  })

  test('status is a valid processing status (not COMPLETED)', () => {
    const { status } = job_state_processing
    expect(VALID_JOB_STATUSES).toContain(status)
    expect(status).not.toBe('COMPLETED')
  })

  test('package_url is null while processing', () => {
    expect(job_state_processing.package_url).toBeNull()
  })

  test('error is null while processing', () => {
    expect(job_state_processing.error).toBeNull()
  })

  test('analysis has episode_title and hook', () => {
    const { analysis } = job_state_processing
    expect(typeof analysis.episode_title).toBe('string')
    expect(analysis.episode_title.length).toBeGreaterThan(0)
    expect(typeof analysis.hook).toBe('string')
    expect(analysis.hook.length).toBeGreaterThan(0)
  })
})

describe('job_state_processing — progress counter', () => {
  test('progress has all required counter fields', () => {
    const { progress } = job_state_processing
    expect(typeof progress.total_artifacts).toBe('number')
    expect(typeof progress.ready).toBe('number')
    expect(typeof progress.processing).toBe('number')
    expect(typeof progress.queued).toBe('number')
    expect(typeof progress.failed).toBe('number')
  })

  test('total_artifacts matches actual artifacts array length', () => {
    const { progress, artifacts } = job_state_processing
    expect(progress.total_artifacts).toBe(artifacts.length)
  })

  test('all artifacts are QUEUED in processing snapshot', () => {
    const { artifacts } = job_state_processing
    for (const a of artifacts) {
      expect(a.status).toBe('QUEUED')
    }
  })
})

describe('job_state_processing — artifacts shape', () => {
  test('every artifact passes shape check', () => {
    for (const artifact of job_state_processing.artifacts) {
      assertArtifactShape(artifact)
    }
  })

  test('all artifact ids are unique', () => {
    const ids = job_state_processing.artifacts.map((a) => a.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

// ---------------------------------------------------------------------------
// job_state_completed
// ---------------------------------------------------------------------------

describe('job_state_completed — top-level shape', () => {
  test('has all required top-level fields', () => {
    const j = job_state_completed
    expect(j).toHaveProperty('job_id')
    expect(j).toHaveProperty('status')
    expect(j).toHaveProperty('progress')
    expect(j).toHaveProperty('analysis')
    expect(j).toHaveProperty('artifacts')
    expect(j).toHaveProperty('package_url')
    expect(j).toHaveProperty('error')
  })

  test('status is COMPLETED', () => {
    expect(job_state_completed.status).toBe('COMPLETED')
  })

  test('package_url is a non-empty string when completed', () => {
    expect(typeof job_state_completed.package_url).toBe('string')
    expect(job_state_completed.package_url.length).toBeGreaterThan(0)
  })

  test('error is null when completed', () => {
    expect(job_state_completed.error).toBeNull()
  })

  test('same job_id in both snapshots', () => {
    expect(job_state_completed.job_id).toBe(job_state_processing.job_id)
  })
})

describe('job_state_completed — progress counters', () => {
  test('ready + failed = total_artifacts', () => {
    const { progress } = job_state_completed
    expect(progress.ready + progress.failed).toBe(progress.total_artifacts)
  })

  test('queued is 0 when completed', () => {
    expect(job_state_completed.progress.queued).toBe(0)
  })
})

describe('job_state_completed — coverage of all artifact types', () => {
  const artifacts = job_state_completed.artifacts
  const byType = (t) => artifacts.filter((a) => a.type === t)

  test('has 5 VIDEO_CLIP artifacts', () => {
    expect(byType('VIDEO_CLIP')).toHaveLength(5)
  })

  test('has exactly 1 LINKEDIN_POST', () => {
    expect(byType('LINKEDIN_POST')).toHaveLength(1)
  })

  test('has exactly 1 TWITTER_THREAD', () => {
    expect(byType('TWITTER_THREAD')).toHaveLength(1)
  })

  test('has exactly 1 SHOW_NOTES', () => {
    expect(byType('SHOW_NOTES')).toHaveLength(1)
  })

  test('has exactly 1 NEWSLETTER', () => {
    expect(byType('NEWSLETTER')).toHaveLength(1)
  })

  test('has exactly 1 YOUTUBE_DESCRIPTION', () => {
    expect(byType('YOUTUBE_DESCRIPTION')).toHaveLength(1)
  })

  test('has at least 1 QUOTE_GRAPHIC', () => {
    expect(byType('QUOTE_GRAPHIC').length).toBeGreaterThanOrEqual(1)
  })

  test('every artifact passes shape check', () => {
    for (const artifact of artifacts) {
      assertArtifactShape(artifact)
    }
  })

  test('all artifact ids are unique', () => {
    const ids = artifacts.map((a) => a.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('job_state_completed — VIDEO_CLIP artifacts', () => {
  const clips = job_state_completed.artifacts.filter((a) => a.type === 'VIDEO_CLIP')

  test('each clip has file_url when READY', () => {
    for (const clip of clips.filter((c) => c.status === 'READY')) {
      expect(typeof clip.file_url).toBe('string')
      expect(clip.file_url.length).toBeGreaterThan(0)
    }
  })

  test('failed clip has null file_url', () => {
    const failed = clips.filter((c) => c.status === 'FAILED')
    for (const clip of failed) {
      expect(clip.file_url).toBeNull()
    }
  })

  test('each READY clip has virality_score and duration_sec in metadata', () => {
    for (const clip of clips.filter((c) => c.status === 'READY')) {
      expect(typeof clip.metadata.virality_score).toBe('number')
      expect(clip.metadata.virality_score).toBeGreaterThanOrEqual(1)
      expect(clip.metadata.virality_score).toBeLessThanOrEqual(10)
      expect(typeof clip.metadata.duration_sec).toBe('number')
    }
  })

  test('clips are indexed 0–4', () => {
    const indices = clips.map((c) => c.index).sort((a, b) => a - b)
    expect(indices).toEqual([0, 1, 2, 3, 4])
  })
})

describe('job_state_completed — text artifact content', () => {
  test('LINKEDIN_POST text_content is a non-empty string', () => {
    const a = job_state_completed.artifacts.find((x) => x.type === 'LINKEDIN_POST')
    expect(typeof a.text_content).toBe('string')
    expect(a.text_content.length).toBeGreaterThan(100)
  })

  test('TWITTER_THREAD text_content is valid JSON with tweets array', () => {
    const a = job_state_completed.artifacts.find((x) => x.type === 'TWITTER_THREAD')
    expect(typeof a.text_content).toBe('string')
    const parsed = JSON.parse(a.text_content)
    expect(Array.isArray(parsed.tweets)).toBe(true)
    expect(parsed.tweets.length).toBeGreaterThanOrEqual(3)
  })

  test('each tweet is at most 280 chars', () => {
    const a = job_state_completed.artifacts.find((x) => x.type === 'TWITTER_THREAD')
    const { tweets } = JSON.parse(a.text_content)
    for (const tweet of tweets) {
      expect(tweet.length).toBeLessThanOrEqual(280)
    }
  })

  test('SHOW_NOTES text_content starts with markdown heading', () => {
    const a = job_state_completed.artifacts.find((x) => x.type === 'SHOW_NOTES')
    expect(a.text_content.trimStart()).toMatch(/^#\s/)
  })

  test('NEWSLETTER text_content is a non-empty string', () => {
    const a = job_state_completed.artifacts.find((x) => x.type === 'NEWSLETTER')
    expect(typeof a.text_content).toBe('string')
    expect(a.text_content.length).toBeGreaterThan(100)
  })

  test('YOUTUBE_DESCRIPTION text_content is a non-empty string', () => {
    const a = job_state_completed.artifacts.find((x) => x.type === 'YOUTUBE_DESCRIPTION')
    expect(typeof a.text_content).toBe('string')
    expect(a.text_content.length).toBeGreaterThan(50)
  })
})

describe('job_state_completed — QUOTE_GRAPHIC artifacts', () => {
  const graphics = job_state_completed.artifacts.filter((a) => a.type === 'QUOTE_GRAPHIC')

  test('each READY graphic has file_url', () => {
    for (const g of graphics.filter((x) => x.status === 'READY')) {
      expect(typeof g.file_url).toBe('string')
      expect(g.file_url.length).toBeGreaterThan(0)
    }
  })

  test('each graphic metadata has quote_text and speaker', () => {
    for (const g of graphics) {
      expect(typeof g.metadata.quote_text).toBe('string')
      expect(g.metadata.quote_text.length).toBeGreaterThan(5)
      expect(typeof g.metadata.speaker).toBe('string')
    }
  })

  test('each graphic metadata has template_id', () => {
    for (const g of graphics) {
      expect(typeof g.metadata.template_id).toBe('string')
    }
  })
})

// ---------------------------------------------------------------------------
// MOCK_SSE_SEQUENCE
// ---------------------------------------------------------------------------

describe('MOCK_SSE_SEQUENCE — structure', () => {
  test('is a non-empty array', () => {
    expect(Array.isArray(MOCK_SSE_SEQUENCE)).toBe(true)
    expect(MOCK_SSE_SEQUENCE.length).toBeGreaterThan(0)
  })

  test('every entry is a [number, string, object] tuple', () => {
    for (const entry of MOCK_SSE_SEQUENCE) {
      expect(Array.isArray(entry)).toBe(true)
      expect(entry).toHaveLength(3)
      const [delay, eventType, payload] = entry
      expect(typeof delay).toBe('number')
      expect(delay).toBeGreaterThanOrEqual(0)
      expect(VALID_SSE_EVENT_TYPES).toContain(eventType)
      expect(typeof payload).toBe('object')
      expect(payload).not.toBeNull()
    }
  })

  test('delays are non-decreasing (events arrive in order)', () => {
    const delays = MOCK_SSE_SEQUENCE.map(([d]) => d)
    for (let i = 1; i < delays.length; i++) {
      expect(delays[i]).toBeGreaterThanOrEqual(delays[i - 1])
    }
  })

  test('first event is status_changed', () => {
    const [, eventType] = MOCK_SSE_SEQUENCE[0]
    expect(eventType).toBe('status_changed')
  })

  test('last event is "completed"', () => {
    const last = MOCK_SSE_SEQUENCE[MOCK_SSE_SEQUENCE.length - 1]
    expect(last[1]).toBe('completed')
  })

  test('sequence includes status GENERATING', () => {
    const statuses = MOCK_SSE_SEQUENCE
      .filter(([, t]) => t === 'status_changed')
      .map(([, , p]) => p.status)
    expect(statuses).toContain('GENERATING')
  })

  test('sequence includes status COMPLETED or completed event', () => {
    const hasCompleted = MOCK_SSE_SEQUENCE.some(([, t]) => t === 'completed')
    expect(hasCompleted).toBe(true)
  })

  test('every artifact_ready event has artifact_id, type, index', () => {
    for (const [, eventType, payload] of MOCK_SSE_SEQUENCE) {
      if (eventType !== 'artifact_ready') continue
      expect(typeof payload.artifact_id).toBe('string')
      expect(VALID_ARTIFACT_TYPES).toContain(payload.type)
      expect(typeof payload.index).toBe('number')
    }
  })

  test('every artifact_failed event has artifact_id and error', () => {
    for (const [, eventType, payload] of MOCK_SSE_SEQUENCE) {
      if (eventType !== 'artifact_failed') continue
      expect(typeof payload.artifact_id).toBe('string')
      expect(typeof payload.error).toBe('string')
      expect(payload.error.length).toBeGreaterThan(0)
    }
  })

  test('completed event has package_url', () => {
    const [, , payload] = MOCK_SSE_SEQUENCE.find(([, t]) => t === 'completed')
    expect(typeof payload.package_url).toBe('string')
    expect(payload.package_url.length).toBeGreaterThan(0)
  })

  test('artifact_ready ids match artifacts in job_state_completed', () => {
    const completedIds = new Set(job_state_completed.artifacts.map((a) => a.id))
    for (const [, eventType, payload] of MOCK_SSE_SEQUENCE) {
      if (eventType !== 'artifact_ready') continue
      expect(completedIds).toContain(payload.artifact_id)
    }
  })

  test('artifact_failed ids match artifacts in job_state_completed', () => {
    const completedIds = new Set(job_state_completed.artifacts.map((a) => a.id))
    for (const [, eventType, payload] of MOCK_SSE_SEQUENCE) {
      if (eventType !== 'artifact_failed') continue
      expect(completedIds).toContain(payload.artifact_id)
    }
  })
})
