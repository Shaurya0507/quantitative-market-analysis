import assert from 'node:assert/strict'
import test from 'node:test'

import { getIngestionPollDelay, pollIngestionJob } from '../src/ingestionPolling.js'

test('polling retrieves the job and forwards its status', async () => {
  const expected = { job_id: 'job-id', ticker: 'HD', status: 'COMPLETED' }
  let resolveUpdate
  const updated = new Promise((resolve) => {
    resolveUpdate = resolve
  })
  const cancel = pollIngestionJob({
    api: { get: async (path) => {
      assert.equal(path, '/ingestion/jobs/job-id')
      return { data: expected }
    } },
    job: { job_id: 'job-id', status: 'PROCESSING' },
    onUpdate: resolveUpdate,
    onError: (error) => {
      throw error
    },
    delay: 1,
  })

  assert.deepEqual(await updated, expected)
  cancel()
})

test('cancelled polling does not update state', async () => {
  let updated = false
  const cancel = pollIngestionJob({
    api: { get: async () => ({ data: {} }) },
    job: { job_id: 'job-id', status: 'QUEUED' },
    onUpdate: () => {
      updated = true
    },
    onError: () => assert.fail('cancelled polling should not report an error'),
    delay: 10,
  })

  cancel()
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.equal(updated, false)
})

test('polling forwards job-status request failures', async () => {
  const expectedError = new Error('status endpoint unavailable')
  let resolveError
  const failed = new Promise((resolve) => {
    resolveError = resolve
  })
  const cancel = pollIngestionJob({
    api: { get: async () => {
      throw expectedError
    } },
    job: { job_id: 'job-id', status: 'PROCESSING' },
    onUpdate: () => assert.fail('failed polling should not update the job'),
    onError: resolveError,
    delay: 1,
  })

  assert.equal(await failed, expectedError)
  cancel()
})

test('rate-limited jobs wait until the provider retry time', () => {
  const now = Date.now()
  const retryAt = new Date(now + 60000).toISOString()

  assert.equal(
    getIngestionPollDelay({ status: 'RATE_LIMITED', next_attempt_at: retryAt }, now),
    60000,
  )
})

test('terminal and missing jobs do not schedule polling', async () => {
  let requests = 0
  const api = { get: async () => {
    requests += 1
    return { data: {} }
  } }
  for (const job of [
    null,
    { job_id: null, status: 'QUEUED' },
    { job_id: 'job-id', status: 'FAILED' },
    { job_id: 'job-id', status: 'COMPLETED' },
  ]) {
    pollIngestionJob({ api, job, onUpdate: () => {}, onError: () => {}, delay: 1 })
  }
  await new Promise((resolve) => setTimeout(resolve, 10))
  assert.equal(requests, 0)
})
