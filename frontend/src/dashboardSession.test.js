import assert from 'node:assert/strict'
import test from 'node:test'

import { getDashboardSessionId } from './dashboardSession.js'

function createStorage() {
  const values = new Map()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
}

test('reuses the session identifier across reloads in the same tab', () => {
  const storage = createStorage()
  let created = 0
  const createId = () => `session-${++created}`

  assert.equal(getDashboardSessionId(storage, createId), 'session-1')
  assert.equal(getDashboardSessionId(storage, createId), 'session-1')
  assert.equal(created, 1)
})

test('creates a new session identifier for a fresh tab session', () => {
  const firstTab = createStorage()
  const secondTab = createStorage()
  let created = 0
  const createId = () => `session-${++created}`

  assert.equal(getDashboardSessionId(firstTab, createId), 'session-1')
  assert.equal(getDashboardSessionId(secondTab, createId), 'session-2')
})
