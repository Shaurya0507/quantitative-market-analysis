import assert from 'node:assert/strict'
import test from 'node:test'

import {
  companyMatchesQuery,
  suggestedCompanies,
  uniqueCompanies,
} from '../src/companySelection.js'

test('company lists deduplicate using ticker identifiers', () => {
  assert.deepEqual(
    uniqueCompanies([
      { ticker: 'AAPL', company_name: 'Apple Inc.' },
      { ticker: 'AAPL', company_name: 'A duplicate Apple record' },
      { ticker: 'MSFT', company_name: 'Microsoft Corporation' },
    ]).map((company) => company.ticker),
    ['AAPL', 'MSFT'],
  )
})

test('company cards prioritize cached stocks and cap the dynamic directory list', () => {
  const cached = [{ ticker: 'NVDA', company_name: 'NVIDIA Corporation' }]
  const directory = Array.from({ length: 10 }, (_, index) => ({
    ticker: `TST${index}`,
    company_name: `Company ${index}`,
  }))

  const cards = suggestedCompanies(cached, directory, 8)

  assert.equal(cards.length, 8)
  assert.equal(cards[0].ticker, 'NVDA')
  assert.equal(cards[0].is_loaded, true)
  assert.equal(new Set(cards.map((company) => company.ticker)).size, 8)
})

test('search matches company names and tickers case-insensitively', () => {
  const company = { ticker: 'CRM', company_name: 'Salesforce, Inc.' }

  assert.equal(companyMatchesQuery(company, 'salesforce'), true)
  assert.equal(companyMatchesQuery(company, 'crm'), true)
  assert.equal(companyMatchesQuery(company, 'microsoft'), false)
})
