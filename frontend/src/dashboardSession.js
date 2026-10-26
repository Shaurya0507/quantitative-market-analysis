const SESSION_STORAGE_KEY = 'marketlens.dashboard-session'

export function getDashboardSessionId(
  storage = window.sessionStorage,
  createId = () => crypto.randomUUID(),
) {
  const existingId = storage.getItem(SESSION_STORAGE_KEY)
  if (existingId) return existingId
  const sessionId = createId()
  storage.setItem(SESSION_STORAGE_KEY, sessionId)
  return sessionId
}
