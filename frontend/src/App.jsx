import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import CompanySelection from './CompanySelectionView'
import { getDashboardSessionId } from './dashboardSession'
import { pollIngestionJob } from './ingestionPolling'
import { WORKSPACE_NAVIGATION } from './workspaceNavigation'
import './App.css'

const PriceChart = lazy(() => import('./PriceChart'))
const QuantitativeWorkspace = lazy(() => import('./QuantitativeWorkspace'))

function formatDate(date) {
  return date.toISOString().slice(0, 10)
}

function getDefaultRange() {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - 90)
  return { start: formatDate(start), end: formatDate(end) }
}

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
  }).format(value)
}

function percent(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${Number(value).toFixed(2)}%`
}

function marketDate(value) {
  if (!value) return '—'
  const [year, month, day] = value.split('-').map(Number)
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  }).format(new Date(year, month - 1, day))
}

function detectionLabel(method) {
  return {
    rolling_z_score: 'Return Z-score',
    relative_volume: 'Relative volume',
    combined_statistical: 'Combined statistical',
  }[method] || method || '—'
}

function formatDetectionDetail(detail) {
  if (detail.method === 'rolling_z_score') {
    return `Z ${Number(detail.value).toFixed(2)} (|Z| ≥ ${Number(detail.threshold).toFixed(2)})`
  }
  if (detail.method === 'relative_volume') {
    return `${Number(detail.value).toFixed(2)}× (≥ ${Number(detail.threshold).toFixed(2)}×)`
  }
  return `${detail.indicator}: ${detail.value ?? '—'}`
}

function App() {
  const [initialRange] = useState(getDefaultRange)
  const [stocks, setStocks] = useState([])
  const [sessionReady, setSessionReady] = useState(false)
  const [sessionError, setSessionError] = useState('')
  const [ticker, setTicker] = useState('')
  const [startDate, setStartDate] = useState(initialRange.start)
  const [endDate, setEndDate] = useState(initialRange.end)
  const [draftStartDate, setDraftStartDate] = useState(initialRange.start)
  const [draftEndDate, setDraftEndDate] = useState(initialRange.end)
  const [dashboard, setDashboard] = useState(null)
  const [performers, setPerformers] = useState([])
  const [anomalies, setAnomalies] = useState([])
  const [anomalySummary, setAnomalySummary] = useState(null)
  const [anomalySeverity, setAnomalySeverity] = useState('')
  const [selectedAnomaly, setSelectedAnomaly] = useState(null)
  const [loadJob, setLoadJob] = useState(null)
  const [dataStatus, setDataStatus] = useState(null)
  const [refreshJobs, setRefreshJobs] = useState([])
  const [refreshUnavailable, setRefreshUnavailable] = useState(false)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [anomalySort, setAnomalySort] = useState({ key: 'price_date', direction: 'desc' })
  const [activeSection, setActiveSection] = useState('anomaly-explorer')
  const sortedAnomalies = useMemo(() => {
    const rows = [...anomalies]
    const { key, direction } = anomalySort
    return rows.sort((left, right) => {
      const a = left[key]
      const b = right[key]
      if (typeof a === 'number' || typeof b === 'number') {
        return ((Number(a) || 0) - (Number(b) || 0)) * (direction === 'asc' ? 1 : -1)
      }
      return String(a ?? '').localeCompare(String(b ?? '')) * (direction === 'asc' ? 1 : -1)
    })
  }, [anomalies, anomalySort])

  useEffect(() => {
    let active = true
    let initialized = false
    let initializing = false
    const sessionId = getDashboardSessionId()
    const heartbeat = () => api.post('/dashboard/session', { session_id: sessionId })
    const initializeSession = () => {
      if (!active || initialized || initializing) return
      initializing = true
      heartbeat()
        .then(() => api.get('/stocks'))
        .then(({ data }) => {
          if (!active) return
          setStocks(data)
          initialized = true
          setSessionError('')
          setSessionReady(true)
        })
        .catch(() => {
          if (active) {
            setSessionError(
              'Could not start a dashboard session. Check that the API and database are running, then refresh this page.',
            )
          }
        })
        .finally(() => {
          initializing = false
        })
    }
    initializeSession()
    const heartbeatTimer = window.setInterval(() => {
      if (!initialized) {
        initializeSession()
        return
      }
      heartbeat()
        .then(({ data }) => {
          if (!active || !data.cache_reset) return
          setTicker('')
          setDashboard(null)
          setPerformers([])
          setAnomalies([])
          setAnomalySummary(null)
          setRefreshJobs([])
          setLoadJob(null)
          return api.get('/stocks').then(({ data: loadedStocks }) => setStocks(loadedStocks))
        })
        .catch(() => {
          if (active) setError('Dashboard session heartbeat failed. Reconnect to keep this session active.')
        })
    }, 15000)
    return () => {
      active = false
      window.clearInterval(heartbeatTimer)
    }
  }, [])

  const isTickerLoaded = stocks.some((stock) => stock.ticker === ticker)
  useEffect(() => {
    if (!ticker || !isTickerLoaded) return undefined
    let active = true
    api.post('/market/refresh', { ticker })
      .then(({ data }) => {
        if (!active) return
        const jobs = data.jobs || []
        setRefreshJobs(jobs)
        setRefreshUnavailable(false)
      })
      .catch(() => {
        if (active) {
          setRefreshUnavailable(true)
        }
      })
    return () => {
      active = false
    }
  }, [ticker, isTickerLoaded])

  useEffect(() => {
    const cleanups = refreshJobs
      .filter((job) => job.job_id && !['COMPLETED', 'FAILED'].includes(job.status))
      .map((job) => pollIngestionJob({
        api,
        job,
        onUpdate: (updatedJob) => {
          setRefreshJobs((current) => current.map((item) => (
            item.job_id === updatedJob.job_id ? updatedJob : item
          )))
          if (updatedJob.status === 'COMPLETED') {
            setRefreshVersion((version) => version + 1)
            setRefreshUnavailable(false)
          }
          if (updatedJob.status === 'FAILED') setRefreshUnavailable(true)
        },
        onError: () => {
          setRefreshUnavailable(true)
        },
      }))
    return () => cleanups.forEach((cleanup) => cleanup())
  }, [refreshJobs])

  useEffect(() => {
    if (!ticker || !isTickerLoaded) return undefined
    let active = true
    Promise.all([
      api.get(`/stocks/${ticker}/history`, { params: { start_date: startDate, end_date: endDate } }),
      api.get(`/stocks/${ticker}/moving-average`, { params: { start_date: startDate, end_date: endDate } }),
      api.get(`/stocks/${ticker}/volatility`, { params: { start_date: startDate, end_date: endDate } }),
      api.get('/market/top-performers', { params: { start_date: startDate, end_date: endDate } }),
      api.get(`/stocks/${ticker}/indicators`, { params: { start_date: startDate, end_date: endDate } }),
      api.get(`/stocks/${ticker}/status`),
    ])
      .then(([historyResponse, averageResponse, volatilityResponse, performersResponse, indicatorsResponse, statusResponse]) => {
        if (!active) return
        const averageByDate = new Map(
          averageResponse.data.map((row) => [row.price_date, Number(row.moving_average_7)]),
        )
        const indicatorsByDate = new Map(
          indicatorsResponse.data.map((row) => [row.price_date, row]),
        )
        const chartData = historyResponse.data.map((row) => ({
          date: row.price_date,
          close: Number(row.close_price),
          movingAverage: averageByDate.get(row.price_date),
          ...(indicatorsByDate.get(row.price_date) || {}),
        }))
        const latest = chartData.at(-1)
        const previous = chartData.at(-2)
        const periodReturn = chartData.length > 1
          ? ((latest.close / chartData[0].close) - 1) * 100
          : null
        const dailyReturn = latest && previous
          ? ((latest.close / previous.close) - 1) * 100
          : null
        setDashboard({
          chartData,
          volatility: volatilityResponse.data.volatility_pct,
          latest,
          periodReturn,
          dailyReturn,
        })
        setPerformers(performersResponse.data)
        setDataStatus(statusResponse.data)
      })
      .catch((requestError) => {
        if (!active) return
        const detail = requestError.response?.data?.detail
        setError(typeof detail === 'string' ? detail : 'Unable to load analytics for this date range.')
        setDashboard(null)
        setPerformers([])
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [ticker, isTickerLoaded, startDate, endDate, refreshVersion])

  useEffect(() => {
    if (!sessionReady) return undefined
    let active = true
    Promise.all([
      api.get('/anomalies/summary'),
      api.get('/anomalies', {
        params: {
          start_date: startDate,
          end_date: endDate,
          severity: anomalySeverity || undefined,
          limit: 100,
        },
      }),
    ])
      .then(([summaryResponse, anomalyResponse]) => {
        if (!active) return
        setAnomalySummary(summaryResponse.data)
        setAnomalies(anomalyResponse.data)
      })
      .catch(() => {
        if (active) {
          setAnomalySummary(null)
          setAnomalies([])
        }
      })
    return () => {
      active = false
    }
  }, [sessionReady, startDate, endDate, anomalySeverity, refreshVersion])

  useEffect(() => {
    if (!loadJob?.job_id || ['COMPLETED', 'FAILED'].includes(loadJob.status)) return undefined
    return pollIngestionJob({
      api,
      job: loadJob,
      onUpdate: (data) => {
        setLoadJob(data)
        if (['FAILED', 'RATE_LIMITED'].includes(data.status)) setLoading(false)
        if (data.status === 'COMPLETED') {
          setLoading(true)
          api.get('/stocks')
            .then(({ data: loadedStocks }) => {
              setStocks(loadedStocks)
              setRefreshVersion((version) => version + 1)
            })
            .catch(() => setError('Stock data loaded, but the stock selector could not be refreshed.'))
          api.get('/anomalies/summary')
            .then(({ data: summary }) => setAnomalySummary(summary))
            .catch(() => setError('Stock data loaded, but the latest market date could not be refreshed.'))
        }
      },
      onError: () => {
        setLoadJob((job) => ({
          ...job,
          status: 'FAILED',
          last_error: 'Unable to retrieve ingestion job status. Please retry the stock search.',
        }))
        setLoading(false)
      },
    })
  }, [loadJob])

  function selectCompany(company) {
    const normalized = company.ticker.toUpperCase()
    setError('')
    const isLoaded = stocks.some((stock) => stock.ticker === normalized)
      || company.is_loaded
      || company.historical_data_available
    if (normalized !== ticker) {
      setDashboard(null)
      setDataStatus(null)
      setLoading(Boolean(isLoaded))
    }
    setTicker(normalized)
    setLoadJob(null)
    if (isLoaded) return
    setDashboard(null)
    setDataStatus(null)
    setLoading(false)
  }

  function loadSelectedCompany() {
    if (!ticker || isTickerLoaded) return
    setError('')
    setLoading(true)
    setLoadJob({ ticker, status: 'QUEUED', job_id: null })
    api.post('/stocks/load', { ticker })
      .then(({ data }) => {
        if (data.status === 'CACHED') {
          setLoadJob(null)
          api.get('/stocks')
            .then(({ data: loadedStocks }) => setStocks(loadedStocks))
            .catch(() => setError('Cached stock data was found, but the stock selector could not be refreshed.'))
          return
        }
        setLoadJob(data)
        if (['FAILED', 'RATE_LIMITED'].includes(data.status)) setLoading(false)
      })
      .catch((requestError) => {
        const detail = requestError.response?.data?.detail
        setLoadJob(null)
        setLoading(false)
        setError(typeof detail === 'string' ? detail : `Could not queue market data for ${ticker}.`)
      })
  }

  function selectDirectoryCompany(company) {
    selectCompany(company)
    setActiveSection('anomaly-explorer')
  }

  function updateRange(event) {
    event.preventDefault()
    if (draftStartDate > draftEndDate) {
      setError('Start date must be on or before end date.')
      return
    }
    setError('')
    if (draftStartDate !== startDate || draftEndDate !== endDate) setLoading(true)
    setStartDate(draftStartDate)
    setEndDate(draftEndDate)
  }

  function sortAnomalies(key) {
    setAnomalySort((current) => ({
      key,
      direction: current.key === key && current.direction === 'desc' ? 'asc' : 'desc',
    }))
  }

  function selectAnomaly(anomaly) {
    setSelectedAnomaly(anomaly)
    if (anomaly?.ticker && anomaly.ticker !== ticker) {
      setTicker(anomaly.ticker)
      setLoading(stocks.some((stock) => stock.ticker === anomaly.ticker))
    }
  }

  const chartData = dashboard?.chartData
  const refreshChecking = refreshJobs.some(
    (job) => ['QUEUED', 'PROCESSING'].includes(job.status),
  )
  const refreshWaiting = refreshJobs.some((job) => job.status === 'RATE_LIMITED')
  const showCompanySelection = ['anomaly-explorer', 'market-data'].includes(activeSection)

  if (!sessionReady) {
    return (
      <main className="app-shell">
        <div className={`notice ${sessionError ? 'error-notice' : 'loading-notice'}`} role={sessionError ? 'alert' : 'status'}>
          {sessionError || 'Starting dashboard session…'}
        </div>
      </main>
    )
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="topbar-right">
          <span className="market-status"><span className="status-dot" /> DAILY MARKET ANALYSIS</span>
          <span className="topbar-date">Latest market data: {marketDate(anomalySummary?.latest_data_date)}</span>
          {refreshChecking && <span className="refresh-status" role="status">Checking for updates…</span>}
          {!refreshChecking && refreshWaiting && !refreshUnavailable && (
            <span className="refresh-status" role="status">Refresh delayed; showing cached data.</span>
          )}
          {refreshUnavailable && <span className="refresh-status">Refresh unavailable; showing cached data.</span>}
          {refreshJobs.some((job) => (
            job.status === 'RATE_LIMITED'
            && job.last_error?.includes('request limit has been reached')
          )) && (
            <span className="refresh-status refresh-quota-status" role="status">
              The market data provider’s request limit has been reached. Cached data remains available.
            </span>
          )}
        </div>
      </header>

      <section className="page-heading">
        <div>
          <h1>Quantitative Market Analysis</h1>
          <p className="subtitle">An interactive software engineering and machine learning project</p>
          <p className="project-attribution">Built by Shaurya Shrivastava</p>
        </div>
      </section>

      <nav className="workspace-nav" aria-label="Quantitative workspace">
        {WORKSPACE_NAVIGATION.map(([section, label]) => (
          <button
            type="button"
            key={section}
            className={activeSection === section ? 'active-workspace-tab' : ''}
            aria-current={activeSection === section ? 'page' : undefined}
            onClick={() => setActiveSection(section)}
          >
            {label}
          </button>
        ))}
      </nav>

      {showCompanySelection && (
        <CompanySelection
          stocks={stocks}
          selectedTicker={ticker}
          onSelectCompany={selectCompany}
          onLoadData={loadSelectedCompany}
          onBrowseDirectory={() => setActiveSection('company-directory')}
          loading={Boolean(loadJob && ['QUEUED', 'PROCESSING'].includes(loadJob.status))}
        />
      )}

      {ticker && isTickerLoaded && <section className="control-row">
        <div className="selected-stock">
          <span className="stock-symbol">{ticker || '—'}</span>
          <span className="stock-name">{stocks.find((stock) => stock.ticker === ticker)?.company_name || 'Select a loaded stock'}</span>
        </div>
        <form className="date-form" onSubmit={updateRange}>
          <label>
            <span>FROM</span>
            <input type="date" value={draftStartDate} onChange={(event) => setDraftStartDate(event.target.value)} />
          </label>
          <span className="date-separator">—</span>
          <label>
            <span>TO</span>
            <input type="date" value={draftEndDate} onChange={(event) => setDraftEndDate(event.target.value)} />
          </label>
          <button type="submit" className="apply-button">Apply range</button>
        </form>
      </section>}

      {error && <div className="notice error-notice" role="alert">{error}</div>}
      {loading && <div className="notice loading-notice" role="status">Loading market data…</div>}
      {loadJob && (
        <div className={`notice job-notice ${loadJob.status === 'FAILED' ? 'error-notice' : ''}`} role="status">
          {loadJob.status === 'QUEUED' && `Market data request for ${loadJob.ticker} is queued.`}
          {loadJob.status === 'PROCESSING' && `Retrieving and analyzing market data for ${loadJob.ticker}…`}
          {loadJob.status === 'RATE_LIMITED' && (
            loadJob.last_error?.includes('request limit has been reached')
              ? 'The market data provider’s request limit has been reached. New stock data cannot be retrieved right now. Please try again later.'
              : 'The provider temporarily delayed this request. Cached market data remains available.'
          )}
          {loadJob.status === 'FAILED' && (loadJob.last_error || `Market data could not be loaded for ${loadJob.ticker}.`)}
        </div>
      )}

      <Suspense fallback={<div className="notice loading-notice">Loading analytics workspace…</div>}>
        {activeSection === 'anomaly-explorer' && (
          <QuantitativeWorkspace
            section={activeSection}
            ticker={ticker}
            isLoaded={isTickerLoaded}
            stocks={stocks}
            refreshVersion={refreshVersion}
            onSelectTicker={selectDirectoryCompany}
          />
        )}
        {activeSection === 'company-directory' && (
          <QuantitativeWorkspace
            section={activeSection}
            ticker={ticker}
            isLoaded={isTickerLoaded}
            stocks={stocks}
            refreshVersion={refreshVersion}
            onSelectTicker={selectDirectoryCompany}
          />
        )}
      </Suspense>
      {activeSection === 'market-data' && (
        <>
      {ticker && isTickerLoaded ? (
        <>
      <section className="metrics-grid" aria-label="Selected stock analytics">
        <article className="metric-card">
          <span className="metric-label">LATEST CLOSE</span>
          <strong>{money(dashboard?.latest?.close)}</strong>
          <span className="metric-foot">{dashboard?.latest?.date || 'Selected period'}</span>
        </article>
        <article className="metric-card">
          <span className="metric-label">PERIOD RETURN</span>
          <strong className={dashboard?.periodReturn >= 0 ? 'positive' : 'negative'}>{percent(dashboard?.periodReturn)}</strong>
          <span className="metric-foot">Close-to-close</span>
        </article>
        <article className="metric-card">
          <span className="metric-label">DAILY RETURN</span>
          <strong className={dashboard?.dailyReturn >= 0 ? 'positive' : 'negative'}>{percent(dashboard?.dailyReturn)}</strong>
          <span className="metric-foot">Latest trading day</span>
        </article>
        <article className="metric-card">
          <span className="metric-label">VOLATILITY</span>
          <strong>{percent(dashboard?.volatility)}</strong>
          <span className="metric-foot">Sample std. deviation</span>
        </article>
      </section>

      <section className="monitor-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">STATISTICAL SIGNALS</p>
            <h2>Market anomaly monitor</h2>
          </div>
          <span className="market-data-note">Daily market observations</span>
        </div>
        <div className="monitor-grid">
          <article className="monitor-card">
            <span>STOCKS MONITORED</span>
            <strong>{anomalySummary?.monitored_stocks ?? '—'}</strong>
          </article>
          <article className="monitor-card">
            <span>ANOMALIES DETECTED</span>
            <strong>{anomalySummary?.total_anomalies ?? '—'}</strong>
          </article>
          <article className="monitor-card">
            <span>HIGH SEVERITY</span>
            <strong className="severity-high-text">{anomalySummary?.high_severity_anomalies ?? '—'}</strong>
          </article>
          <article className="monitor-card">
            <span>LATEST DATA DATE</span>
            <strong className="monitor-date">{anomalySummary?.latest_data_date || '—'}</strong>
          </article>
        </div>
        <p className="disclaimer">
          Anomalies are observations that differ from established statistical patterns.
        </p>
      </section>

      <section className="content-grid">
        <article className="panel chart-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">PRICE HISTORY</p>
              <h2>{ticker || '—'} closing price</h2>
            </div>
            <div className="chart-legend">
              <span><i className="legend-close" /> Close</span>
              <span><i className="legend-average" /> 7-day average</span>
            </div>
          </div>
          {dashboard?.chartData?.length ? (
            <div className="chart-wrap">
              <Suspense fallback={<div className="chart-loading">Loading chart…</div>}>
                <PriceChart
                  data={chartData}
                  money={money}
                  onAnomalyClick={selectAnomaly}
                />
              </Suspense>
            </div>
          ) : (
            <div className="empty-chart">{loading ? 'Fetching price history…' : 'Price history will appear here.'}</div>
          )}
          <div className="chart-footnote">Daily observations · USD · date range is inclusive</div>
          <div className="availability-strip">
            <span><b>Provider</b> {dataStatus?.stock?.data_source || '—'}</span>
            <span><b>Cache</b> {dataStatus?.stock ? 'PostgreSQL' : 'Not loaded'}</span>
            <span><b>Status</b> {dataStatus?.job?.status || dataStatus?.stock?.ingestion_status || '—'}</span>
          </div>
          {selectedAnomaly?.ticker === ticker && (
            <div className="anomaly-detail">
              <div className="detail-heading">
                <div>
                  <p className="eyebrow">WHY WAS THIS FLAGGED?</p>
                  <h3>{selectedAnomaly.ticker} · {selectedAnomaly.price_date}</h3>
                </div>
                <span className={`severity-badge severity-${selectedAnomaly.severity?.toLowerCase()}`}>
                  {selectedAnomaly.severity}
                </span>
              </div>
              <p>{selectedAnomaly.explanation}</p>
              <p className="anomaly-detector-line">
                Detector: <strong>{detectionLabel(selectedAnomaly.detection_method)}</strong>
                {selectedAnomaly.model_version && ` · rules ${selectedAnomaly.model_version}`}
              </p>
              {!!selectedAnomaly.detection_details?.length && (
                <div className="anomaly-trigger-details">
                  {selectedAnomaly.detection_details.map((detail) => (
                    <span key={`${detail.method}-${detail.indicator}`}>
                      {detectionLabel(detail.method)}: {formatDetectionDetail(detail)}
                    </span>
                  ))}
                </div>
              )}
              <div className="detail-metrics">
                <span>Close <b>{money(selectedAnomaly.close)}</b></span>
                <span>Daily return <b>{percent(Number(selectedAnomaly.daily_return) * 100)}</b></span>
                <span>Return Z-score <b>{selectedAnomaly.return_z_score == null ? 'Insufficient history' : Number(selectedAnomaly.return_z_score).toFixed(2)}</b></span>
                <span>Volume ratio <b>{selectedAnomaly.volume_ratio == null ? 'Insufficient history' : `${Number(selectedAnomaly.volume_ratio).toFixed(2)}×`}</b></span>
                <span>20-day volatility <b>{percent(Number(selectedAnomaly.rolling_volatility) * 100)}</b></span>
              </div>
            </div>
          )}
          {selectedAnomaly?.ticker === ticker && (
            <div className="detail-data-note">
              Price/volume indicators are calculated from unadjusted provider bars and may be affected by stock splits.
            </div>
          )}
        </article>

        <article className="panel comparison-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">PERFORMANCE</p>
              <h2>Stock comparison</h2>
            </div>
            <span className="period-tag">Selected period</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>RANK</th><th>STOCK</th><th>RETURN</th></tr>
              </thead>
              <tbody>
                {performers.map((stock) => (
                  <tr key={stock.ticker} className={stock.ticker === ticker ? 'active-row' : ''}>
                    <td><span className="rank-pill">{String(stock.performance_rank).padStart(2, '0')}</span></td>
                    <td>
                      <button className="ticker-link" type="button" onClick={() => selectCompany(stock)}>
                        <strong>{stock.ticker}</strong>
                        <small>{stock.company_name}</small>
                      </button>
                    </td>
                    <td className={Number(stock.period_return_pct) >= 0 ? 'positive' : 'negative'}>
                      {percent(stock.period_return_pct)}
                    </td>
                  </tr>
                ))}
                {!loading && !performers.length && (
                  <tr><td colSpan="3" className="empty-cell">No comparison data for this range.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="comparison-note">Ranked by first-to-last closing price return</div>
        </article>
      </section>

      <section className="panel anomaly-panel">
        <div className="panel-heading anomaly-panel-heading">
          <div>
            <p className="eyebrow">MARKET ANOMALY MONITOR</p>
            <h2>Anomaly leaderboard</h2>
          </div>
          <label className="severity-filter">
            <span>SEVERITY</span>
            <select value={anomalySeverity} onChange={(event) => setAnomalySeverity(event.target.value)}>
              <option value="">All severities</option>
              <option value="HIGH">High</option>
              <option value="MEDIUM">Medium</option>
              <option value="LOW">Low</option>
            </select>
          </label>
        </div>
        <div className="table-wrap anomaly-table-wrap">
          <table>
            <thead>
              <tr>
                <th><button onClick={() => sortAnomalies('ticker')}>TICKER</button></th>
                <th>COMPANY</th>
                <th><button onClick={() => sortAnomalies('price_date')}>DATE</button></th>
                <th><button onClick={() => sortAnomalies('daily_return')}>DAILY RETURN</button></th>
                <th><button onClick={() => sortAnomalies('return_z_score')}>RETURN Z</button></th>
                <th><button onClick={() => sortAnomalies('volume_ratio')}>VOLUME ×</button></th>
                <th>DETECTION</th>
                <th>SEVERITY</th>
              </tr>
            </thead>
            <tbody>
              {sortedAnomalies.map((anomaly) => (
                <tr
                  key={`${anomaly.ticker}-${anomaly.price_date}`}
                  className={selectedAnomaly?.ticker === anomaly.ticker && selectedAnomaly?.price_date === anomaly.price_date ? 'selected-anomaly-row' : ''}
                  onClick={() => selectAnomaly(anomaly)}
                >
                  <td><strong>{anomaly.ticker}</strong></td>
                  <td className="company-cell">{anomaly.company_name}</td>
                  <td>{anomaly.price_date}</td>
                  <td className={anomaly.daily_return >= 0 ? 'positive' : 'negative'}>{percent(anomaly.daily_return * 100)}</td>
                  <td>{anomaly.return_z_score == null ? '—' : Number(anomaly.return_z_score).toFixed(2)}</td>
                  <td>{anomaly.volume_ratio == null ? '—' : `${Number(anomaly.volume_ratio).toFixed(2)}×`}</td>
                  <td>
                    <div className="detection-trigger-list">
                      {(anomaly.detection_details || []).map((detail) => (
                        <span key={`${detail.method}-${detail.indicator}`}>
                          <strong>{detectionLabel(detail.method)}</strong>
                          {formatDetectionDetail(detail)}
                        </span>
                      ))}
                      {!anomaly.detection_details?.length && detectionLabel(anomaly.detection_method)}
                      <small>{anomaly.model_version}</small>
                    </div>
                  </td>
                  <td><span className={`severity-badge severity-${anomaly.severity.toLowerCase()}`}>{anomaly.severity}</span></td>
                </tr>
              ))}
              {!sortedAnomalies.length && (
                <tr><td colSpan="8" className="empty-cell">No anomalies detected in this date range.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="comparison-note">
          Statistical alerts use prior observations only. No alert is assigned when indicator history is insufficient.
        </div>
      </section>
        </>
      ) : (
        <section className="market-data-empty">
          <h2>{ticker ? 'Market data is needed' : 'Select a company to view Market Data'}</h2>
          <p>
            {ticker
              ? 'Load data for this company to see its prices, returns, and volatility.'
              : 'Choose a company from the cards or search above to explore daily prices, returns, and volatility.'}
          </p>
        </section>
      )}
        </>
      )}

      <footer className="footer">
        <span>Developed by Shaurya Shrivastava</span>
        <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">API documentation ↗</a>
      </footer>
    </main>
  )
}

export default App
