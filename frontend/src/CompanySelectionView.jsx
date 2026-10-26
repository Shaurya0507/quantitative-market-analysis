import { useEffect, useMemo, useState } from 'react'

import { api } from './api'
import {
  companyMatchesQuery,
  suggestedCompanies,
  uniqueCompanies,
} from './companySelection'

const TICKER_PATTERN = /^[A-Z][A-Z0-9.-]{0,9}$/

export default function CompanySelection({
  stocks,
  selectedTicker,
  onSelectCompany,
  onLoadData,
  onBrowseDirectory,
  loading,
}) {
  const [directoryCompanies, setDirectoryCompanies] = useState([])
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searchError, setSearchError] = useState('')
  const [directoryError, setDirectoryError] = useState('')
  const [showResults, setShowResults] = useState(false)

  useEffect(() => {
    let active = true
    api.get('/stocks/directory', { params: { limit: 100 } })
      .then(({ data }) => {
        if (!active) return
        setDirectoryCompanies(data.items)
        setDirectoryError('')
      })
      .catch(() => {
        if (active) setDirectoryError('Company suggestions are temporarily unavailable.')
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    const searchTerm = query.trim()
    if (!searchTerm) return undefined
    let active = true
    const timer = window.setTimeout(() => {
      api.get('/stocks/search', { params: { q: searchTerm } })
        .then(({ data }) => {
          if (!active) return
          const cachedMatches = stocks.filter((company) => companyMatchesQuery(company, searchTerm))
          const results = uniqueCompanies([...data, ...cachedMatches])
          const normalizedTicker = searchTerm.toUpperCase()
          if (
            !results.some((company) => company.ticker === normalizedTicker)
            && TICKER_PATTERN.test(normalizedTicker)
          ) {
            results.push({
              ticker: normalizedTicker,
              company_name: normalizedTicker,
              is_loaded: stocks.some((company) => company.ticker === normalizedTicker),
              historical_data_available: stocks.some((company) => company.ticker === normalizedTicker),
            })
          }
          setSearchResults(results)
          setSearchError('')
        })
        .catch(() => {
          if (active) {
            setSearchResults([])
            setSearchError('Unable to search companies right now.')
          }
        })
    }, 250)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [query, stocks])

  const cards = useMemo(
    () => suggestedCompanies(stocks, directoryCompanies),
    [stocks, directoryCompanies],
  )
  const selectedCompany = uniqueCompanies([
    ...stocks,
    ...directoryCompanies,
    ...searchResults,
  ]).find((company) => company.ticker === selectedTicker)
  const selectedIsLoaded = stocks.some((stock) => stock.ticker === selectedTicker)
    || Boolean(selectedCompany?.historical_data_available)

  function selectCompany(company) {
    onSelectCompany(company)
    setQuery('')
    setSearchResults([])
    setShowResults(false)
  }

  function submitTicker(event) {
    event.preventDefault()
    const normalizedTicker = query.trim().toUpperCase()
    if (!TICKER_PATTERN.test(normalizedTicker)) return
    const matchingCompany = searchResults.find((company) => company.ticker === normalizedTicker)
      || stocks.find((company) => company.ticker === normalizedTicker)
      || directoryCompanies.find((company) => company.ticker === normalizedTicker)
      || { ticker: normalizedTicker, company_name: normalizedTicker, is_loaded: false }
    selectCompany(matchingCompany)
  }

  return (
    <section className="company-selection" aria-labelledby="company-selection-heading">
      <div className="company-selection-heading">
        <div>
          <p className="eyebrow">COMPANY SELECTION</p>
          <h2 id="company-selection-heading">
            {selectedTicker ? 'Choose a Company' : 'Select a Company to Analyze'}
          </h2>
          {!selectedTicker && (
            <p>
              Explore statistical anomalies in daily stock market data. Choose a company below
              or search for another publicly traded company.
            </p>
          )}
        </div>
        <button type="button" className="directory-browse-button" onClick={onBrowseDirectory}>
          Browse All Companies
        </button>
      </div>

      <form className="company-search-form" onSubmit={submitTicker}>
        <label htmlFor="company-search">SEARCH BY COMPANY NAME OR TICKER</label>
        <div className="company-search-control">
          <span aria-hidden="true">⌕</span>
          <input
            id="company-search"
            value={query}
            onFocus={() => setShowResults(true)}
            onChange={(event) => {
              setQuery(event.target.value)
              if (!event.target.value.trim()) {
                setSearchResults([])
                setSearchError('')
              }
              setShowResults(true)
            }}
            placeholder="Search by company name or ticker (e.g., Salesforce or CRM)"
            autoComplete="off"
          />
          <button type="submit">Search</button>
        </div>
        {showResults && query.trim() && (
          <div className="company-search-results" role="listbox" aria-label="Company search results">
            {searchResults.map((company) => {
              const isLoaded = stocks.some((stock) => stock.ticker === company.ticker)
                || company.is_loaded
                || company.historical_data_available
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={company.ticker === selectedTicker}
                  key={company.ticker}
                  onClick={() => selectCompany(company)}
                >
                  <strong>{company.ticker}</strong>
                  <span>{company.company_name}</span>
                  <small>{isLoaded ? 'Ready to Analyze' : 'Data Not Loaded'}</small>
                </button>
              )
            })}
            {!searchResults.length && !searchError && (
              <p className="company-search-empty">No matching companies found.</p>
            )}
            {searchError && <p className="company-search-empty">{searchError}</p>}
          </div>
        )}
      </form>

      {directoryError && <p className="company-selection-error" role="status">{directoryError}</p>}
      <div className="company-card-grid">
        {cards.map((company) => {
          const isLoaded = stocks.some((stock) => stock.ticker === company.ticker)
            || company.is_loaded
          return (
            <button
              className={`company-card ${company.ticker === selectedTicker ? 'selected-company-card' : ''}`}
              type="button"
              key={company.ticker}
              aria-pressed={company.ticker === selectedTicker}
              onClick={() => selectCompany(company)}
            >
              <strong>{company.ticker}</strong>
              <span>{company.company_name}</span>
              <small>{isLoaded ? 'Ready to Analyze' : 'Data Not Loaded'}</small>
            </button>
          )
        })}
        {!cards.length && <p className="company-selection-error">Loading company suggestions…</p>}
      </div>

      {selectedTicker && !selectedIsLoaded && (
        <div className="company-load-prompt">
          <div>
            <strong>
              {selectedCompany?.company_name || selectedTicker} — Data Not Loaded
            </strong>
            <p>Load daily market data to generate statistical analysis and anomaly detection results.</p>
          </div>
          <button
            type="button"
            className="apply-button"
            disabled={loading}
            onClick={onLoadData}
          >
            {loading ? 'Loading Data…' : 'Load Data'}
          </button>
        </div>
      )}

      {selectedTicker && selectedIsLoaded && (
        <p className="selected-company-status" role="status">
          {selectedCompany?.company_name || selectedTicker} · Ready to Analyze
        </p>
      )}
    </section>
  )
}
