import assert from 'node:assert/strict'
import test from 'node:test'

import { getIngestionPollDelay, pollIngestionJob } from './ingestionPolling.js'

test('polls ordinary ingestion jobs every two seconds', () => {
  assert.equal(getIngestionPollDelay({ status: 'QUEUED' }), 2000)
  assert.equal(getIngestionPollDelay({ status: 'PROCESSING' }), 2000)
})

test('waits until the provider retry time for a rate-limited job', () => {
  const now = Date.parse('2026-01-01T00:00:00Z')
  const job = {
    status: 'RATE_LIMITED',
    next_attempt_at: '2026-01-01T00:05:00Z',
  }

  assert.equal(getIngestionPollDelay(job, now), 300000)
  assert.equal(
    getIngestionPollDelay({ ...job, next_attempt_at: 'invalid' }, now),
    30000,
  )
})

test('does not poll a terminal job or a response without a job id', () => {
  let scheduled = false
  const originalSetTimeout = globalThis.setTimeout
  globalThis.setTimeout = () => {
    scheduled = true
    return 1
  }
  try {
    const poll = (job) => pollIngestionJob({
      api: { get: () => Promise.resolve({ data: job }) },
      job,
      onUpdate: () => {},
      onError: () => {},
    })

    poll({ status: 'COMPLETED', job_id: 'finished' })
    poll({ status: 'QUEUED', job_id: null })
    assert.equal(scheduled, false)
  } finally {
    globalThis.setTimeout = originalSetTimeout
  }
})
