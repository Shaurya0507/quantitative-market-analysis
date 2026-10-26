const POLL_INTERVAL_MS = 2000
const RATE_LIMIT_FALLBACK_MS = 30000

export function getIngestionPollDelay(job, now = Date.now()) {
  if (job.status !== 'RATE_LIMITED') return POLL_INTERVAL_MS
  const retryAt = Date.parse(job.next_attempt_at)
  return Number.isNaN(retryAt)
    ? RATE_LIMIT_FALLBACK_MS
    : Math.max(POLL_INTERVAL_MS, retryAt - now)
}

export function pollIngestionJob({ api, job, onUpdate, onError, delay }) {
  if (!job?.job_id || ['COMPLETED', 'FAILED'].includes(job.status)) {
    return () => {}
  }

  let active = true
  const timer = setTimeout(() => {
    api.get(`/ingestion/jobs/${job.job_id}`)
      .then(({ data }) => {
        if (active) onUpdate(data)
      })
      .catch((error) => {
        if (active) onError(error)
      })
  }, delay ?? getIngestionPollDelay(job))

  return () => {
    active = false
    clearTimeout(timer)
  }
}
