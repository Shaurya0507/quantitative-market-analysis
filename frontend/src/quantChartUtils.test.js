import assert from 'node:assert/strict'
import test from 'node:test'

import { formatReturnPercent, getDateAxisInterval } from './quantChartUtils.js'

test('converts decimal daily returns into readable percentage labels', () => {
  assert.equal(formatReturnPercent(-0.1), '-10%')
  assert.equal(formatReturnPercent(-0.05), '-5%')
  assert.equal(formatReturnPercent(0), '0%')
  assert.equal(formatReturnPercent(0.05), '5%')
  assert.equal(formatReturnPercent(-0.001), '-0.1%')
})

test('limits date axis labels without changing chart data length', () => {
  assert.equal(getDateAxisInterval(0), 0)
  assert.equal(getDateAxisInterval(8), 0)
  assert.equal(getDateAxisInterval(100), 12)
  assert.equal(getDateAxisInterval(100, 5), 19)
})
