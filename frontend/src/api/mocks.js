// Mock fixtures for Day 1–3 (no real API yet).
// Replace imports of these with real API calls on Day 4.

const JOB_ID = 'mock-job-1234-5678-abcd'

// ---------------------------------------------------------------------------
// Artifact fixtures — one per ArtifactType from SPEC §1.2
// ---------------------------------------------------------------------------

const MOCK_ARTIFACTS = [
  // VIDEO_CLIP × 5
  {
    id: 'art-vid-0',
    type: 'VIDEO_CLIP',
    index: 0,
    status: 'READY',
    file_url: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4',
    text_content: null,
    metadata: { virality_score: 9, duration_sec: 52.0, hook_text: 'The dirty secret of AI valuations' },
    version: 1,
  },
  {
    id: 'art-vid-1',
    type: 'VIDEO_CLIP',
    index: 1,
    status: 'READY',
    file_url: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4',
    text_content: null,
    metadata: { virality_score: 7, duration_sec: 45.0, hook_text: 'Why founders ignore the real costs' },
    version: 1,
  },
  {
    id: 'art-vid-2',
    type: 'VIDEO_CLIP',
    index: 2,
    status: 'READY',
    file_url: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4',
    text_content: null,
    metadata: { virality_score: 6, duration_sec: 38.0, hook_text: 'The 3AM engineering decision' },
    version: 1,
  },
  {
    id: 'art-vid-3',
    type: 'VIDEO_CLIP',
    index: 3,
    status: 'READY',
    file_url: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4',
    text_content: null,
    metadata: { virality_score: 8, duration_sec: 60.0, hook_text: 'What VCs really mean by "scalable"' },
    version: 1,
  },
  {
    id: 'art-vid-4',
    type: 'VIDEO_CLIP',
    index: 4,
    status: 'FAILED',
    file_url: null,
    text_content: null,
    metadata: { virality_score: 5, duration_sec: 30.0 },
    version: 1,
  },

  // LINKEDIN_POST
  {
    id: 'art-li-0',
    type: 'LINKEDIN_POST',
    index: 0,
    status: 'READY',
    file_url: null,
    text_content: `Most AI startups are building on sand — here's why.

I had a long conversation with Sarah Chen (CTO @ Anthropic Labs) last week. She said something that stuck with me:

"You can't outrun technical debt with valuation."

At first it sounds obvious. Of course you can't. But think about what's actually happening in the market right now.

Companies are raising Series A rounds on GPT wrapper demos. The cap table looks great, the deck is clean, the vision is compelling. And underneath it all? A rat's nest of prompt hacks duct-taped to an OpenAI key.

Sarah's seen this pattern in three different cycles — the cloud rush, the mobile rush, now AI. The companies that survive aren't the fastest to ship. They're the ones who made the unglamorous bet on solid infrastructure when everyone else was chasing headlines.

Three things she said every AI founder needs to hear:

1. Your moat is not your model. Your moat is your data flywheel and your ops.
2. Inference cost matters at scale in ways your spreadsheet doesn't capture yet.
3. The hardest conversation you'll ever have is explaining to your board why Q3's "technical debt sprint" isn't actually a sprint.

Worth listening to the full episode if you're building anything in this space.

#AI #Startups #TechnicalDebt #FounderAdvice #MachineLearning`,
    metadata: { word_count: 218, tone: 'analytical' },
    version: 1,
  },

  // TWITTER_THREAD
  {
    id: 'art-tw-0',
    type: 'TWITTER_THREAD',
    index: 0,
    status: 'READY',
    file_url: null,
    text_content: JSON.stringify({
      tweets: [
        'Most AI startups are building on sand. Here\'s the uncomfortable truth nobody says out loud 🧵',
        '"You can\'t outrun technical debt with valuation." — @sarahchen_cto dropped this on our podcast and I haven\'t stopped thinking about it.',
        'What does that actually mean? Series A founders are raising on GPT wrapper demos with no real data moat. The clock is ticking.',
        'Sarah\'s seen 3 tech cycles: cloud, mobile, AI. The survivors weren\'t the fastest shippers. They made the unglamorous infra bet early.',
        'The 3 things every AI founder needs to hear (from someone who\'s seen the inside of 40+ AI startups):',
        '1/ Your moat is NOT your model. It\'s your data flywheel + ops. Models commoditize. Proprietary data doesn\'t.',
        '2/ Inference cost at scale is a spreadsheet blindspot. $0.002/call sounds cheap until you\'re doing 50M calls/day.',
        '3/ "Technical debt sprint" is an oxymoron. It\'s a slog, and your board won\'t understand it until it\'s a crisis.',
        'Full episode with Sarah Chen dropping in bio. Worth 45 min. 🎙️ {{EPISODE_URL}}\n\n🧵 End',
      ],
    }),
    metadata: { tweet_count: 9, tone: 'casual' },
    version: 1,
  },

  // SHOW_NOTES
  {
    id: 'art-sn-0',
    type: 'SHOW_NOTES',
    index: 0,
    status: 'READY',
    file_url: null,
    text_content: `# The Hidden Cost of AI Hype

> Most AI startups are building on sand — here's why.

## About the guest

**Sarah Chen** is CTO at Anthropic Labs with 15 years of experience in ML infrastructure. She's advised over 40 AI startups on scaling their engineering orgs and has seen three major technology cycles from the inside.

## Topics covered

- Why valuation can't outrun technical debt
- The infrastructure bets that separate survivors from casualties
- Inference cost at scale: what spreadsheets miss
- How to have the "technical debt sprint" conversation with your board

## Timestamps

- [00:00] Introduction & Sarah's background
- [03:00] The AI hype cycle vs. previous tech waves
- [12:00] What "building on sand" actually looks like
- [23:45] The three infrastructure decisions that matter
- [35:10] How to talk to your board about technical debt
- [44:30] Rapid fire: tools Sarah actually uses
- [48:00] Where to find Sarah

## Notable quotes

> "You can't outrun technical debt with valuation" — Sarah Chen

> "Your moat is not your model. Your moat is your data flywheel and your ops." — Sarah Chen

## Links mentioned

- Anthropic Labs: https://anthropic.com
- Sarah on LinkedIn: {{SARAH_LINKEDIN}}`,
    metadata: { tone: 'analytical' },
    version: 1,
  },

  // NEWSLETTER
  {
    id: 'art-nl-0',
    type: 'NEWSLETTER',
    index: 0,
    status: 'READY',
    file_url: null,
    text_content: `**Subject line:** The thing VCs won't tell you about AI technical debt

---

If you've been following the AI funding frenzy, you've probably noticed something: the companies raising the biggest rounds aren't always the ones with the strongest foundations.

This week I sat down with Sarah Chen, CTO at Anthropic Labs, to dig into what she calls "building on sand" — and how to avoid it.

### Why Technical Debt Moves Faster in AI

Traditional technical debt accumulates slowly. AI technical debt compounds. When your core product is a prompt chain calling a third-party API, every architectural shortcut becomes load-bearing the moment you hit scale.

Sarah's rule: if you can't explain your system's failure modes in plain English, you don't understand your own product yet.

### The Three Bets That Matter

**Bet 1: Data flywheel over model quality.**
Models commoditize. Proprietary data doesn't. The startups that survive are the ones who figured out how to collect, clean, and leverage their own data before the next model drop made their prompt engineering obsolete.

**Bet 2: Inference cost is an iceberg.**
$0.002 per call sounds negligible. At 50 million calls per day, it's $3,650 per day — $1.3M per year — before you've built any margin. Sarah has watched founders get blindsided by this in month 18.

**Bet 3: Ship boring infrastructure early.**
The most impactful thing you can do in month 3 is build observability, not features. You need to know when your model is hallucinating before your users do.

---

Worth listening to the full episode for the board conversation framework alone.

→ [Listen now] {{EPISODE_URL}}`,
    metadata: { tone: 'casual', word_count: 280 },
    version: 1,
  },

  // YOUTUBE_DESCRIPTION
  {
    id: 'art-yt-0',
    type: 'YOUTUBE_DESCRIPTION',
    index: 0,
    status: 'READY',
    file_url: null,
    text_content: `Most AI startups are building on sand — here's what Sarah Chen (CTO @ Anthropic Labs) says founders must fix before it's too late.

00:00 Introduction & Sarah's background
03:00 The AI hype cycle vs. previous tech waves
12:00 What "building on sand" actually looks like
23:45 The three infrastructure decisions that matter
35:10 How to talk to your board about technical debt
44:30 Rapid fire: tools Sarah actually uses
48:00 Where to find Sarah

Keywords: AI startups, technical debt, ML infrastructure, founder advice, AI hype, Anthropic, scaling engineering

{{PODCAST_LINKS}}`,
    metadata: { tone: 'analytical' },
    version: 1,
  },

  // QUOTE_GRAPHIC × 3
  {
    id: 'art-qg-0',
    type: 'QUOTE_GRAPHIC',
    index: 0,
    status: 'READY',
    file_url: 'https://via.placeholder.com/1080x1080/0a0a0a/ffffff?text=Quote+1',
    text_content: null,
    metadata: {
      quote_text: "You can't outrun technical debt with valuation",
      speaker: 'Sarah Chen',
      template_id: 'minimal_dark',
      source_quote_index: 0,
    },
    version: 1,
  },
  {
    id: 'art-qg-1',
    type: 'QUOTE_GRAPHIC',
    index: 1,
    status: 'READY',
    file_url: 'https://via.placeholder.com/1080x1080/f5f5f5/111111?text=Quote+2',
    text_content: null,
    metadata: {
      quote_text: 'Your moat is not your model. Your moat is your data flywheel.',
      speaker: 'Sarah Chen',
      template_id: 'minimal_light',
      source_quote_index: 1,
    },
    version: 1,
  },
  {
    id: 'art-qg-2',
    type: 'QUOTE_GRAPHIC',
    index: 2,
    status: 'READY',
    file_url: 'https://via.placeholder.com/1080x1080/1a1a2e/e0e0e0?text=Quote+3',
    text_content: null,
    metadata: {
      quote_text: 'Inference cost is an iceberg.',
      speaker: 'Sarah Chen',
      template_id: 'minimal_dark',
      source_quote_index: 2,
    },
    version: 1,
  },
]

// ---------------------------------------------------------------------------
// Job state snapshots
// ---------------------------------------------------------------------------

export const job_state_processing = {
  job_id: JOB_ID,
  status: 'GENERATING',
  progress: {
    total_artifacts: MOCK_ARTIFACTS.length,
    ready: 0,
    processing: 2,
    queued: MOCK_ARTIFACTS.length - 2,
    failed: 0,
  },
  analysis: {
    episode_title: 'The Hidden Cost of AI Hype',
    hook: "Most AI startups are building on sand — here's why.",
  },
  artifacts: MOCK_ARTIFACTS.map((a) => ({ ...a, status: 'QUEUED', file_url: null })),
  package_url: null,
  error: null,
}

export const job_state_completed = {
  job_id: JOB_ID,
  status: 'COMPLETED',
  progress: {
    total_artifacts: MOCK_ARTIFACTS.length,
    ready: MOCK_ARTIFACTS.filter((a) => a.status === 'READY').length,
    processing: MOCK_ARTIFACTS.filter((a) => a.status === 'PROCESSING').length,
    queued: 0,
    failed: MOCK_ARTIFACTS.filter((a) => a.status === 'FAILED').length,
  },
  analysis: {
    episode_title: 'The Hidden Cost of AI Hype',
    hook: "Most AI startups are building on sand — here's why.",
  },
  artifacts: MOCK_ARTIFACTS,
  package_url: `/media/packages/podcast_pack_${JOB_ID}_20260423.zip`,
  error: null,
}

// ---------------------------------------------------------------------------
// Simulated SSE event sequence for useJob mock mode
// ---------------------------------------------------------------------------

// Each entry: [delay_ms, event_type, payload]
export const MOCK_SSE_SEQUENCE = [
  [500,  'status_changed',  { status: 'INGESTING' }],
  [1500, 'status_changed',  { status: 'TRANSCRIBING' }],
  [3000, 'status_changed',  { status: 'ANALYZING' }],
  [5000, 'status_changed',  { status: 'GENERATING' }],
  [5500, 'artifact_ready',  { artifact_id: 'art-li-0',  type: 'LINKEDIN_POST',        index: 0 }],
  [6000, 'artifact_ready',  { artifact_id: 'art-tw-0',  type: 'TWITTER_THREAD',       index: 0 }],
  [6500, 'artifact_ready',  { artifact_id: 'art-sn-0',  type: 'SHOW_NOTES',           index: 0 }],
  [7000, 'artifact_ready',  { artifact_id: 'art-nl-0',  type: 'NEWSLETTER',           index: 0 }],
  [7200, 'artifact_ready',  { artifact_id: 'art-yt-0',  type: 'YOUTUBE_DESCRIPTION',  index: 0 }],
  [8000, 'artifact_ready',  { artifact_id: 'art-vid-0', type: 'VIDEO_CLIP',           index: 0 }],
  [8500, 'artifact_ready',  { artifact_id: 'art-vid-1', type: 'VIDEO_CLIP',           index: 1 }],
  [9000, 'artifact_ready',  { artifact_id: 'art-vid-2', type: 'VIDEO_CLIP',           index: 2 }],
  [9500, 'artifact_ready',  { artifact_id: 'art-vid-3', type: 'VIDEO_CLIP',           index: 3 }],
  [9800, 'artifact_failed', { artifact_id: 'art-vid-4', error: 'FFmpeg clip extraction failed' }],
  [10000,'artifact_ready',  { artifact_id: 'art-qg-0',  type: 'QUOTE_GRAPHIC',        index: 0 }],
  [10300,'artifact_ready',  { artifact_id: 'art-qg-1',  type: 'QUOTE_GRAPHIC',        index: 1 }],
  [10600,'artifact_ready',  { artifact_id: 'art-qg-2',  type: 'QUOTE_GRAPHIC',        index: 2 }],
  [11000,'status_changed',  { status: 'PACKAGING' }],
  [12000,'completed',       { package_url: `/media/packages/podcast_pack_${JOB_ID}_20260423.zip` }],
]
