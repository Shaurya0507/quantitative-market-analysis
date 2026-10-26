import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { WORKSPACE_NAVIGATION } from '../src/workspaceNavigation.js'

test('workspace has exactly the three requested tabs in order', () => {
  assert.deepEqual(WORKSPACE_NAVIGATION, [
    ['anomaly-explorer', 'Anomaly Explorer'],
    ['market-data', 'Market Data'],
    ['company-directory', 'Company Directory'],
  ])
})

test('removed comparison page and unnecessary top labels stay absent', async () => {
  const app = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8')
  const workspace = await readFile(
    new URL('../src/QuantitativeWorkspace.jsx', import.meta.url),
    'utf8',
  )

  assert.doesNotMatch(app, /Model Comparison|model-comparison|SOFTWARE ENGINEERING · MACHINE LEARNING/)
  assert.doesNotMatch(workspace, /Three detectors\. One historical record\.|function ModelComparison/)
  assert.match(workspace, /<h1>Quantitative Anomaly Explorer<\/h1>/)
})
