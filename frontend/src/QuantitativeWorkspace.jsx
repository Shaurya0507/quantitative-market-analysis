import { useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Label,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api } from './api'
import { formatReturnPercent, getDateAxisInterval } from './quantChartUtils'

const METHOD_LABELS = {
  rolling_z_score: 'Rolling Z-Score',
  relative_volume: 'Relative Volume',
  local_outlier_factor: 'Local Outlier Factor',
}

function percent(value) {
  return value == null || !Number.isFinite(Number(value))
    ? '—'
    : `${(Number(value) * 100).toFixed(2)}%`
}

function number(value, digits = 2) {
  return value == null || !Number.isFinite(Number(value))
    ? '—'
    : Number(value).toFixed(digits)
}

function makeHistogram(values, selectedReturn, mean, standardDeviation) {
  if (!values.length) return []
  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const binCount = Math.min(20, Math.max(6, Math.ceil(Math.sqrt(values.length))))
  const width = (maximum - minimum) / binCount || 1
  const bins = Array.from({ length: binCount }, (_, index) => ({
    midpoint: minimum + (index + 0.5) * width,
    count: 0,
  }))
  values.forEach((value) => {
    const index = Math.min(binCount - 1, Math.floor((value - minimum) / width))
    bins[index].count += 1
  })
  const selected = Math.min(binCount - 1, Math.floor((selectedReturn - minimum) / width))
  return bins.map((bin, index) => ({
    ...bin,
    selected: index === selected,
    label: `${(bin.midpoint * 100).toFixed(1)}%`,
    mean: mean * 100,
    lowerThreshold: (mean - 2.5 * standardDeviation) * 100,
    upperThreshold: (mean + 2.5 * standardDeviation) * 100,
  }))
}

function SummaryCard({ method, run, result, settings }) {
  const insufficient = run?.status === 'INSUFFICIENT_DATA' || run?.status === 'NOT_RUN'
  let scoreLabel = 'Latest eligible result'
  let score = number(result?.anomaly_score)
  let threshold = result?.threshold ?? settings?.threshold ?? settings?.shock_threshold
  let secondary = `Alert threshold ±${number(threshold)}`
  let question = 'How unusual was this observation?'
  let formula = 'The score is compared with a method-specific threshold.'
  let rationale = 'The score is compared with a method-specific threshold to classify unusual observations.'
  if (method === 'relative_volume') {
    question = 'Was trading activity unusually high?'
    scoreLabel = 'Latest relative volume'
    score = `${number(result?.anomaly_score)}×`
    secondary = `Alert at ${number(threshold)}× prior ${settings?.window || 20}-day average`
    formula = 'Relative Volume = Current Volume / Historical Average Volume'
    rationale = 'A ratio of 3.0 means current volume was three times its prior-period average.'
  } else if (method === 'local_outlier_factor') {
    question = 'Does this trading day resemble typical market behavior?'
    scoreLabel = 'LOF anomaly score'
    secondary = `Exploratory threshold ${number(threshold)}`
    formula = 'Local Outlier Factor compares the local density of a point with its neighbors.'
    rationale = `The model evaluates ${settings?.feature_names?.join(', ') || 'daily return, relative volume, and intraday range'}. Its ${settings?.minimum_training_observations || 50}-observation initial training sample makes these scores exploratory.`
  } else if (method === 'rolling_z_score') {
    question = 'How unusual was the stock’s daily return?'
    scoreLabel = 'Latest eligible Z-score'
    formula = 'Z = (r − μ) / σ'
    rationale = `A daily return is compared with the mean and sample standard deviation of the prior ${settings?.window || 60} trading-day returns.`
  }
  return (
    <article className="quant-model-card">
      <p className="eyebrow">DETECTOR</p>
      <h3>{METHOD_LABELS[method]}</h3>
      <p className="model-question">{question}</p>
      {insufficient ? (
        <div className="model-insufficient">
          <strong>{run?.status === 'NOT_RUN' ? 'NOT PROCESSED' : 'INSUFFICIENT HISTORY'}</strong>
          <span>{run?.message || 'Process this stock to check eligibility.'}</span>
        </div>
      ) : run?.status === 'FAILED' ? (
        <div className="model-insufficient">
          <strong>MODEL UNAVAILABLE</strong>
          <span>{run.message || 'The model did not produce a reliable result.'}</span>
        </div>
      ) : (
        <>
          <span className="model-value-label">{scoreLabel}</span>
          <strong className="model-value">{score}</strong>
          <div className="model-card-footer">
            <span className={`model-classification ${result?.classification === 'ANOMALY' ? 'is-anomaly' : ''}`}>
              {result?.classification || 'NO EVALUATION'}
            </span>
            <span>{secondary}</span>
          </div>
          <div className="model-version">VERSION {run?.model_version || 'v2'}</div>
        </>
      )}
      <details className="model-method-details">
        <summary>How it works</summary>
        <p>{rationale}</p>
        <code>{formula}</code>
        {method === 'rolling_z_score' && (
          <p><b>r</b>: daily return · <b>μ</b>: prior-window mean · <b>σ</b>: prior-window sample standard deviation.</p>
        )}
        {method === 'local_outlier_factor' && (
          <p>It identifies combinations of features that are sparse relative to nearby observations; it does not forecast prices.</p>
        )}
      </details>
    </article>
  )
}

function ChartPanel({ title, subtitle, children, className = '' }) {
  return (
    <article className={`panel quant-chart-panel ${className}`}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{subtitle}</p>
          <h2>{title}</h2>
        </div>
      </div>
      {children}
    </article>
  )
}

function AnomalyExplanation({ selection, ticker }) {
  if (!selection) {
    return (
      <div className="quant-explanation empty-explanation">
        Select a model result or flagged date to inspect its calculation.
      </div>
    )
  }
  const { method, price_date: priceDate, features = {}, anomaly_score: score, threshold, classification } = selection
  const isAnomaly = classification === 'ANOMALY'
  let formula = 'This method compares its score with a method-specific threshold.'
  let calculation = `Measured score ${number(score)}; threshold ${number(threshold)}.`
  let interpretation = isAnomaly
    ? 'This observation crossed the method’s threshold and was flagged for statistical review.'
    : 'This observation did not cross the method’s threshold; that does not mean every market characteristic was typical.'
  if (method === 'rolling_z_score') {
    const direction = Number(features.daily_return) >= Number(features.historical_mean)
      ? 'above'
      : 'below'
    formula = 'Z = (r − μ) / σ, using the preceding 60 trading-day returns.'
    calculation = `Daily return ${percent(features.daily_return)}; baseline mean ${percent(features.historical_mean)} and sample standard deviation ${percent(features.historical_stddev)}. Z = ${number(score)}; threshold |Z| ≥ ${number(threshold)}.`
    interpretation = isAnomaly
      ? `The return was ${number(Math.abs(Number(score)))} standard deviations ${direction} its recent mean, exceeding the threshold. The detector classified this observation as anomalous.`
      : `The return was ${number(Math.abs(Number(score)))} standard deviations from its recent mean, below the alert threshold. The detector classified this observation as normal.`
  } else if (method === 'relative_volume') {
    formula = 'Relative Volume = Current Volume / Historical Average Volume.'
    calculation = `Current volume ${number(features.current_volume, 0)} / prior ${features.volume_observations || 20}-day average ${number(features.historical_volume_mean, 0)} = ${number(score)}×; threshold ${number(threshold)}×.`
    interpretation = isAnomaly
      ? `Volume was ${number(score)} times the historical baseline, meeting or exceeding the alert threshold.`
      : `Volume was ${number(score)} times the historical baseline, below the alert threshold.`
  } else if (method === 'local_outlier_factor') {
    const featureList = Object.entries(features)
      .map(([key, value]) => `${key}: ${number(value, 4)}`)
      .join(' · ')
    formula = 'LOF compares an observation’s local density with that of its nearest neighbors.'
    calculation = `Exploratory anomaly score ${number(score)}; fitted threshold ${number(threshold)}. Features: ${featureList}.`
    interpretation = isAnomaly
      ? `The observation’s local density score crossed the fitted threshold, so its feature combination was classified as unusual relative to neighboring observations.`
      : `The observation’s score did not cross the fitted threshold; its feature combination was not flagged as unusual by this exploratory model.`
  }
  return (
    <div className="quant-explanation">
      <div className="detail-heading">
        <div>
          <p className="eyebrow">{classification === 'ANOMALY' ? 'WHY WAS THIS FLAGGED?' : 'OBSERVATION DETAILS'}</p>
          <h3>{ticker} · {priceDate} · {METHOD_LABELS[method]}</h3>
        </div>
        <span className={`severity-badge ${classification === 'ANOMALY' ? 'severity-high' : 'severity-low'}`}>
          {classification}
        </span>
      </div>
      <p className="explanation-formula">{formula}</p>
      <p>{calculation}</p>
      <p className="explanation-interpretation">{interpretation}</p>
    </div>
  )
}

function ModelCharts({ indicators, methods, onSelect }) {
  const zRun = methods.find((item) => item.method === 'rolling_z_score')
  const volumeRun = methods.find((item) => item.method === 'relative_volume')
  const lofRun = methods.find((item) => item.method === 'local_outlier_factor')
  const zByDate = new Map((zRun?.results || []).map((row) => [row.price_date, row]))
  const volumeByDate = new Map((volumeRun?.results || []).map((row) => [row.price_date, row]))
  const lofByDate = new Map((lofRun?.results || []).map((row) => [row.price_date, row]))
  const indicatorRows = indicators.filter((row) => row.daily_return != null)
  const zTimeline = (zRun?.results || []).map((row) => ({
    ...row,
    z: Number(row.anomaly_score),
  }))
  const volumeTimeline = (volumeRun?.results || []).map((row) => ({
    ...row,
    ratio: Number(row.anomaly_score),
  }))
  const volatilityTimeline = indicators.filter((row) => row.rolling_volatility != null).map((row) => ({
    ...row,
    rolling: Number(row.rolling_volatility) * 100,
    baseline: row.historical_volatility_mean == null
      ? null
      : Number(row.historical_volatility_mean) * 100,
  }))
  const lofTimeline = (lofRun?.results || []).map((row) => ({
    ...row,
    score: Number(row.anomaly_score),
  }))
  const scatterRows = indicatorRows
    .filter((row) => row.volume_ratio != null)
    .map((row) => ({
      ...row,
      x: Number(row.daily_return) * 100,
      y: Number(row.volume_ratio),
      isZAnomaly: zByDate.get(row.price_date)?.classification === 'ANOMALY',
      isVolumeAnomaly: volumeByDate.get(row.price_date)?.classification === 'ANOMALY',
      isLofAnomaly: lofByDate.get(row.price_date)?.classification === 'ANOMALY',
      zResult: zByDate.get(row.price_date),
      volumeResult: volumeByDate.get(row.price_date),
      lofResult: lofByDate.get(row.price_date),
    }))
  const returns = indicators
    .map((row) => Number(row.daily_return))
    .filter(Number.isFinite)
  const latestResult = zRun?.results?.at(-1)
  const mean = Number(latestResult?.features?.historical_mean || 0)
  const standardDeviation = Number(latestResult?.features?.historical_stddev || 0)
  const histogram = makeHistogram(returns, Number(latestResult?.features?.daily_return || 0), mean, standardDeviation)
  const volumeUnavailableCount = indicatorRows.filter((row) => row.volume_ratio == null).length
  const zThreshold = zRun?.settings?.threshold ?? 2.5
  const volumeThreshold = volumeRun?.settings?.threshold ?? 2.5

  return (
    <>
      <div className="quant-chart-grid">
        <ChartPanel title="Return Z-Score Timeline" subtitle="PRIOR 60 TRADING-DAY BASELINE" className="quant-chart-full-width">
          {zTimeline.length ? (
            <div className="quant-chart">
              <ResponsiveContainer>
                <ComposedChart data={zTimeline}>
                  <CartesianGrid stroke="#202a37" vertical={false} />
                  <XAxis
                    dataKey="price_date"
                    interval={getDateAxisInterval(zTimeline.length)}
                    minTickGap={24}
                    tickFormatter={(value) => value.slice(5)}
                  >
                    <Label value="Trading date" position="insideBottom" offset={-4} />
                  </XAxis>
                  <YAxis>
                    <Label value="Return z-score (standard deviations)" angle={-90} position="insideLeft" />
                  </YAxis>
                  <Tooltip labelFormatter={(value) => `Date: ${value}`} />
                  <ReferenceLine y={zThreshold} stroke="#ff837f" strokeDasharray="4 4" />
                  <ReferenceLine y={-zThreshold} stroke="#ff837f" strokeDasharray="4 4" />
                  <Line
                    type="monotone"
                    dataKey="z"
                    stroke="#65d8b0"
                    dot={(props) => (
                      <circle
                        {...props}
                        r={props.payload.classification === 'ANOMALY' ? 4 : 2}
                        fill={props.payload.classification === 'ANOMALY' ? '#ff837f' : '#65d8b0'}
                        onClick={() => onSelect(props.payload)}
                      />
                    )}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          ) : <p className="chart-empty">Z-scores require at least 60 prior daily returns.</p>}
          <p className="chart-caption">
            Compares each daily return with its prior 60-session baseline. Values beyond ±{number(zThreshold)} are alerts; red points mark flagged dates.
          </p>
        </ChartPanel>

        <ChartPanel title="Relative Volume Timeline" subtitle="PRIOR 20-DAY AVERAGE">
          {volumeTimeline.length ? (
            <>
              <div className="quant-chart">
                <ResponsiveContainer>
                  <ComposedChart data={volumeTimeline}>
                    <CartesianGrid stroke="#202a37" vertical={false} />
                    <XAxis
                      dataKey="price_date"
                      interval={getDateAxisInterval(volumeTimeline.length)}
                      minTickGap={24}
                      tickFormatter={(value) => value.slice(5)}
                    >
                      <Label value="Trading date" position="insideBottom" offset={-4} />
                    </XAxis>
                    <YAxis>
                      <Label value="Volume ratio (× prior 20-day average)" angle={-90} position="insideLeft" />
                    </YAxis>
                    <Tooltip labelFormatter={(value) => `Date: ${value}`} />
                    <ReferenceLine y={volumeThreshold} stroke="#ff837f" strokeDasharray="4 4" />
                    <Line type="monotone" dataKey="ratio" name="Current / prior average volume" stroke="#83a6ff" dot={false} />
                    <Scatter
                      data={volumeTimeline.filter((row) => row.classification === 'ANOMALY')}
                      dataKey="ratio"
                      fill="#ff837f"
                      onClick={(entry) => entry?.payload && onSelect(entry.payload)}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <p className="chart-caption">
                Compares each session’s volume with its prior 20-session average. Ratios at or above {number(volumeThreshold)}× trigger alerts; red points are flagged.
              </p>
            </>
          ) : <p className="chart-empty">Relative volume requires 20 prior trading-day volume observations.</p>}
        </ChartPanel>

        <ChartPanel title="Rolling Volatility" subtitle="20-DAY SAMPLE VOLATILITY · DESCRIPTIVE">
          {volatilityTimeline.length ? (
            <>
              <div className="quant-chart">
                <ResponsiveContainer>
                  <ComposedChart data={volatilityTimeline}>
                    <CartesianGrid stroke="#202a37" vertical={false} />
                    <XAxis
                      dataKey="price_date"
                      interval={getDateAxisInterval(volatilityTimeline.length)}
                      minTickGap={24}
                      tickFormatter={(value) => value.slice(5)}
                    >
                      <Label value="Trading date" position="insideBottom" offset={-4} />
                    </XAxis>
                    <YAxis tickFormatter={(value) => `${Number(value).toFixed(1)}%`}>
                      <Label value="Daily-return volatility (%)" angle={-90} position="insideLeft" />
                    </YAxis>
                    <Tooltip
                      labelFormatter={(value) => `Date: ${value}`}
                      formatter={(value, name) => [percent(Number(value) / 100), name]}
                    />
                    <Line type="monotone" dataKey="rolling" name="20-day rolling volatility" stroke="#65d8b0" dot={false} />
                    <Line type="monotone" dataKey="baseline" name="Prior 60-observation mean" stroke="#83a6ff" dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <p className="chart-caption">
                Shows the 20-session rolling sample standard deviation of daily returns against its prior 60-session mean. Higher values indicate larger recent swings; this is descriptive, not an alert.
              </p>
            </>
          ) : <p className="chart-empty">Rolling volatility requires 20 daily-return observations.</p>}
        </ChartPanel>

        <ChartPanel title="Return vs. Relative Volume" subtitle="OBSERVATIONS WITH A COMPLETE VOLUME BASELINE">
          {scatterRows.length ? (
            <div className="quant-chart">
              <ResponsiveContainer>
                <ScatterChart>
                  <CartesianGrid stroke="#202a37" />
                  <XAxis type="number" dataKey="x" name="Daily return" unit="%">
                    <Label value="Daily return (%)" position="insideBottom" offset={-4} />
                  </XAxis>
                  <YAxis type="number" dataKey="y" name="Relative volume">
                    <Label value="Volume ratio (×)" angle={-90} position="insideLeft" />
                  </YAxis>
                  <Tooltip
                    cursor={{ strokeDasharray: '3 3' }}
                    formatter={(value, name) => [
                      name === 'Daily return' ? `${Number(value).toFixed(2)}%` : `${Number(value).toFixed(2)}×`,
                      name,
                    ]}
                  />
                  <Scatter
                    name="Typical observations"
                    data={scatterRows.filter((row) => !row.isZAnomaly && !row.isVolumeAnomaly)}
                    fill="#6f8bab"
                    onClick={(entry) => entry?.payload?.zResult && onSelect(entry.payload.zResult)}
                  />
                  <Scatter
                    name="Statistical alerts"
                    data={scatterRows.filter((row) => row.isZAnomaly || row.isVolumeAnomaly)}
                    fill="#ff837f"
                    onClick={(entry) => {
                      const result = entry?.payload?.zResult || entry?.payload?.volumeResult
                      if (result) onSelect(result)
                    }}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          ) : <p className="chart-empty">No observations have a complete return and volume baseline.</p>}
          <p className="chart-caption">
            Each point is one trading session. Red points crossed the return z-score or volume-ratio alert threshold; {volumeUnavailableCount} sessions are omitted because a prior-volume baseline is unavailable.
          </p>
        </ChartPanel>

        <ChartPanel title="Daily Return Distribution" subtitle="EMPIRICAL HISTOGRAM">
          {histogram.length ? (
            <>
              <div className="quant-chart">
                <ResponsiveContainer>
                  <BarChart data={histogram}>
                    <CartesianGrid stroke="#202a37" vertical={false} />
                    <XAxis
                      dataKey="midpoint"
                      type="number"
                      tickCount={5}
                      tickFormatter={formatReturnPercent}
                    >
                      <Label value="Daily return (%)" position="insideBottom" offset={-4} />
                    </XAxis>
                    <YAxis>
                      <Label value="Trading sessions" angle={-90} position="insideLeft" />
                    </YAxis>
                    <Tooltip
                      labelFormatter={(value) => `Return: ${formatReturnPercent(value)}`}
                      formatter={(value) => [value, 'Trading sessions']}
                    />
                    <ReferenceLine x={mean * 100} stroke="#83a6ff" label="mean" />
                    <ReferenceLine x={(mean - 2.5 * standardDeviation) * 100} stroke="#ff837f" strokeDasharray="4 4" />
                    <ReferenceLine x={(mean + 2.5 * standardDeviation) * 100} stroke="#ff837f" strokeDasharray="4 4" />
                    <Bar dataKey="count" fill="#65d8b0" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="chart-caption">
                Bars count sessions in each return range. Blue marks the prior-window mean; dashed lines mark ±2.5 standard deviations. Selected return {percent(latestResult?.features?.daily_return)} · empirical percentile {
                  latestResult
                    ? `${(returns.filter((value) => value <= Number(latestResult.features.daily_return)).length / returns.length * 100).toFixed(1)}%`
                    : '—'
                } · standard deviation {percent(standardDeviation)}. No normality assumption.
              </p>
            </>
          ) : <p className="chart-empty">No daily return observations available.</p>}
        </ChartPanel>

        <ChartPanel title="LOF Novelty Score Timeline" subtitle="EXPLORATORY · FIXED INITIAL TRAINING WINDOW">
          {lofTimeline.length ? (
            <>
              <div className="quant-chart">
                <ResponsiveContainer>
                  <ComposedChart data={lofTimeline}>
                    <CartesianGrid stroke="#202a37" vertical={false} />
                    <XAxis
                      dataKey="price_date"
                      interval={getDateAxisInterval(lofTimeline.length)}
                      minTickGap={24}
                      tickFormatter={(value) => value.slice(5)}
                    >
                      <Label value="Trading date" position="insideBottom" offset={-4} />
                    </XAxis>
                    <YAxis>
                      <Label value="LOF anomaly score" angle={-90} position="insideLeft" />
                    </YAxis>
                    <Tooltip labelFormatter={(value) => `Date: ${value}`} />
                    <ReferenceLine y={lofTimeline[0].threshold} stroke="#ffb454" strokeDasharray="4 4" />
                    <Line
                      type="monotone"
                      dataKey="score"
                      name="LOF anomaly score"
                      stroke="#ffb454"
                      dot={(props) => (
                        <circle
                          {...props}
                          r={props.payload.classification === 'ANOMALY' ? 4 : 2}
                          fill={props.payload.classification === 'ANOMALY' ? '#ff837f' : '#ffb454'}
                          onClick={() => onSelect(props.payload)}
                        />
                      )}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <p className="chart-caption">
                Compares each eligible session’s novelty score with the fitted threshold (dashed line). Red points exceed it; results are exploratory, not predictive.
              </p>
            </>
          ) : <p className="chart-empty">LOF needs at least 50 eligible training observations and 10 later observations.</p>}
        </ChartPanel>

        <ChartPanel title="LOF Feature Projection" subtitle="DAILY RETURN × RELATIVE VOLUME">
          {lofRun?.results?.length ? (
            <>
              <div className="quant-chart">
                <ResponsiveContainer>
                  <ScatterChart>
                    <CartesianGrid stroke="#202a37" />
                    <XAxis type="number" dataKey="x" name="Daily return" unit="%">
                      <Label value="Daily return (%)" position="insideBottom" offset={-4} />
                    </XAxis>
                    <YAxis type="number" dataKey="y" name="Relative volume">
                      <Label value="Volume ratio (×)" angle={-90} position="insideLeft" />
                    </YAxis>
                    <Tooltip
                      formatter={(value, name) => [
                        name === 'Daily return' ? `${Number(value).toFixed(2)}%` : `${Number(value).toFixed(2)}×`,
                        name,
                      ]}
                    />
                    <Scatter
                      name="Normal"
                      data={scatterRows.filter((row) => row.lofResult && !row.isLofAnomaly)}
                      fill="#6f8bab"
                      onClick={(entry) => entry?.payload?.lofResult && onSelect(entry.payload.lofResult)}
                    />
                    <Scatter
                      name="LOF anomaly"
                      data={scatterRows.filter((row) => row.lofResult && row.isLofAnomaly)}
                      fill="#ffb454"
                      onClick={(entry) => entry?.payload?.lofResult && onSelect(entry.payload.lofResult)}
                    />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
              <p className="chart-caption">
                Each point is one eligible session; amber points exceed the LOF threshold. The model also uses intraday range, which is not shown on these axes.
              </p>
            </>
          ) : <p className="chart-empty">LOF results are unavailable until the historical training minimum is met.</p>}
        </ChartPanel>
      </div>
    </>
  )
}

function AnomalyGuide() {
  return (
    <section className="anomaly-intro" aria-labelledby="understanding-anomalies">
      <div className="anomaly-intro-heading">
        <p className="eyebrow">PROJECT GUIDE</p>
        <h2 id="understanding-anomalies">Understanding Market Anomalies</h2>
      </div>
      <div className="anomaly-intro-grid">
        <article>
          <h3>What is an anomaly?</h3>
          <p>A daily observation that differs substantially from typical financial patterns—for example, an unusually large return, elevated volume, or an uncommon combination of indicators. It is not automatically an error; it marks behavior for closer statistical examination.</p>
        </article>
        <article>
          <h3>Why analyze them?</h3>
          <p>Comparing observations with recent behavior adds context: was the return unusually large or was volume above its baseline?</p>
        </article>
        <article>
          <h3>How this project works</h3>
          <p>PostgreSQL processes daily market data; Rolling Z-Score, Relative Volume, and exploratory Local Outlier Factor examine complementary statistical signals.</p>
        </article>
      </div>
    </section>
  )
}

function AnomalyExplorer({ ticker, isLoaded, refreshVersion }) {
  const [analysis, setAnalysis] = useState(null)
  const [indicators, setIndicators] = useState([])
  const [selectedResult, setSelectedResult] = useState(null)
  const [loadedTicker, setLoadedTicker] = useState(null)
  const [error, setError] = useState('')
  const [processing, setProcessing] = useState(false)
  const methods = analysis?.ticker === ticker ? analysis.methods : []
  const loading = Boolean(ticker && isLoaded && loadedTicker !== ticker)

  useEffect(() => {
    if (!ticker || !isLoaded) return undefined
    let active = true
    Promise.all([
      api.get(`/stocks/${ticker}/models`),
      api.get(`/stocks/${ticker}/indicators`),
    ])
      .then(([modelResponse, indicatorResponse]) => {
        if (!active) return
        setError('')
        setAnalysis(modelResponse.data)
        setIndicators(indicatorResponse.data)
      })
      .catch((requestError) => {
        if (active) setError(requestError.response?.data?.detail || 'Could not load quantitative analysis.')
      })
      .finally(() => {
        if (active) setLoadedTicker(ticker)
      })
    return () => {
      active = false
    }
  }, [ticker, isLoaded, refreshVersion])

  async function processModels() {
    setProcessing(true)
    setError('')
    try {
      await api.post(`/stocks/${ticker}/models/reprocess`)
      const [modelResponse, indicatorResponse] = await Promise.all([
        api.get(`/stocks/${ticker}/models`),
        api.get(`/stocks/${ticker}/indicators`),
      ])
      setAnalysis(modelResponse.data)
      setIndicators(indicatorResponse.data)
      setLoadedTicker(ticker)
    } catch (requestError) {
      setError(requestError.response?.data?.detail || 'Model processing failed.')
    } finally {
      setProcessing(false)
    }
  }

  const latest = Object.fromEntries(
    methods.map((model) => [model.method, model.results.at(-1)]),
  )
  const displayedResult = selectedResult?.ticker === ticker
    ? selectedResult
    : latest.rolling_z_score
  const needsProcessing = methods.length === 0
    || methods.some((model) => ['NOT_RUN', 'FAILED'].includes(model.status))

  if (!ticker || !isLoaded) {
    return (
      <section className="quant-workspace">
        <AnomalyGuide />
      </section>
    )
  }

  return (
    <section className="quant-workspace">
      <div className="quant-page-heading">
        <div>
          <p className="eyebrow">QUANTITATIVE ANOMALY EXPLORER / {ticker}</p>
          <h1>Quantitative Anomaly Explorer</h1>
          <p>Z-score, relative volume, and exploratory LOF use prior-only baselines; rolling volatility is descriptive.</p>
        </div>
        {needsProcessing && (
          <button className="apply-button" type="button" disabled={processing || loading} onClick={processModels}>
            {processing ? 'Processing models…' : 'Process models'}
          </button>
        )}
      </div>
      {error && <div className="notice error-notice" role="alert">{error}</div>}
      <AnomalyGuide />
      <div className="quant-model-grid">
        <SummaryCard method="rolling_z_score" run={methods.find((item) => item.method === 'rolling_z_score')?.run || { status: methods.find((item) => item.method === 'rolling_z_score')?.status || 'NOT_RUN', message: methods.find((item) => item.method === 'rolling_z_score')?.message }} result={latest.rolling_z_score} settings={methods.find((item) => item.method === 'rolling_z_score')?.settings} />
        <SummaryCard method="relative_volume" run={methods.find((item) => item.method === 'relative_volume')?.run || { status: methods.find((item) => item.method === 'relative_volume')?.status || 'NOT_RUN', message: methods.find((item) => item.method === 'relative_volume')?.message }} result={latest.relative_volume} settings={methods.find((item) => item.method === 'relative_volume')?.settings} />
        <SummaryCard method="local_outlier_factor" run={methods.find((item) => item.method === 'local_outlier_factor')?.run || { status: methods.find((item) => item.method === 'local_outlier_factor')?.status || 'NOT_RUN', message: methods.find((item) => item.method === 'local_outlier_factor')?.message }} result={latest.local_outlier_factor} settings={methods.find((item) => item.method === 'local_outlier_factor')?.settings} />
      </div>
      {loading ? (
        <div className="notice loading-notice">Loading model results…</div>
      ) : (
        <>
          <ModelCharts indicators={indicators} methods={methods} onSelect={setSelectedResult} />
          <AnomalyExplanation selection={displayedResult} ticker={ticker} />
        </>
      )}
    </section>
  )
}

function CompanyDirectory({ stocks, onSelectTicker, refreshVersion }) {
  const [query, setQuery] = useState('')
  const [sector, setSector] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState({ items: [], total: 0, sectors: [] })
  const [error, setError] = useState('')
  const limit = 25

  useEffect(() => {
    let active = true
    api.get('/stocks/directory', {
      params: { q: query || undefined, sector: sector || undefined, limit, offset },
    })
      .then(({ data }) => {
        if (active) {
          setPage(data)
          setError('')
        }
      })
      .catch(() => {
        if (active) setError('Unable to load the company directory.')
      })
    return () => {
      active = false
    }
  }, [query, sector, offset, refreshVersion])

  function changeQuery(value) {
    setQuery(value)
    setOffset(0)
  }

  function changeSector(value) {
    setSector(value)
    setOffset(0)
  }

  return (
    <section className="quant-workspace">
      <div className="quant-page-heading">
        <div>
          <p className="eyebrow">100-COMPANY DIRECTORY</p>
          <h1>Company Directory</h1>
          <p>Curated large U.S.-listed companies across major sectors. Catalog membership does not mean data has been loaded or analyzed.</p>
        </div>
      </div>
      {error && <div className="notice error-notice" role="alert">{error}</div>}
      <div className="directory-controls">
        <label>
          <span>SEARCH BY COMPANY OR TICKER</span>
          <input value={query} onChange={(event) => changeQuery(event.target.value)} placeholder="e.g. Home Depot or HD" />
        </label>
        <label>
          <span>SECTOR</span>
          <select value={sector} onChange={(event) => changeSector(event.target.value)}>
            <option value="">All sectors</option>
            {page.sectors.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
      </div>
      <div className="table-wrap directory-table">
        <table>
          <thead><tr><th>TICKER</th><th>COMPANY</th><th>SECTOR</th><th>EXCHANGE</th><th>DATA SOURCE</th><th>HISTORY</th><th>ACTION</th></tr></thead>
          <tbody>
            {page.items.map((company) => {
              const loaded = stocks.some((stock) => stock.ticker === company.ticker) || company.historical_data_available
              return (
                <tr key={company.ticker}>
                  <td><strong>{company.ticker}</strong></td>
                  <td>{company.company_name}</td>
                  <td>{company.sector}</td>
                  <td>{company.exchange}</td>
                  <td>{loaded ? company.data_source : 'Not loaded'}</td>
                  <td>{loaded ? `Through ${company.latest_available_date}` : 'Not analyzed'}</td>
                  <td>
                    <button type="button" className="directory-action" onClick={() => onSelectTicker(company)}>
                      {loaded ? 'Analyze' : 'Select'}
                    </button>
                  </td>
                </tr>
              )
            })}
            {!page.items.length && <tr><td colSpan="7" className="empty-cell">No companies match these filters.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="directory-pagination">
        <span>{page.total ? `${offset + 1}–${Math.min(offset + limit, page.total)} of ${page.total}` : '0 companies'}</span>
        <div>
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</button>
          <button type="button" disabled={offset + limit >= page.total} onClick={() => setOffset(offset + limit)}>Next</button>
        </div>
      </div>
    </section>
  )
}

export default function QuantitativeWorkspace({
  section,
  ticker,
  isLoaded,
  stocks,
  refreshVersion,
  onSelectTicker,
}) {
  if (section === 'company-directory') {
    return <CompanyDirectory stocks={stocks} refreshVersion={refreshVersion} onSelectTicker={onSelectTicker} />
  }
  return <AnomalyExplorer ticker={ticker} isLoaded={isLoaded} refreshVersion={refreshVersion} />
}
