import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

function PriceChart({ data, money, onAnomalyClick }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 15, right: 14, bottom: 0, left: 4 }}>
        <CartesianGrid stroke="#202a37" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(value) => value.slice(5)}
          stroke="#69788b"
          tickLine={false}
          axisLine={false}
          minTickGap={35}
          tick={{ fill: '#8290a2', fontSize: 11 }}
        />
        <YAxis
          domain={['auto', 'auto']}
          tickFormatter={(value) => `$${value}`}
          stroke="#69788b"
          tickLine={false}
          axisLine={false}
          width={66}
          tick={{ fill: '#8290a2', fontSize: 11 }}
        />
        <Tooltip
          labelFormatter={(value) => value}
          formatter={(value, name) => [money(value), name === 'close' ? 'Close' : '7-day average']}
          contentStyle={{ background: '#111a25', border: '1px solid #2b3746', borderRadius: 8 }}
          labelStyle={{ color: '#dbe4ee' }}
        />
        <Line
          type="monotone"
          dataKey="close"
          stroke="#64d9b2"
          strokeWidth={2}
          activeDot={{ r: 4 }}
          dot={(props) => {
            if (!props.payload?.is_anomaly) return null
            const color = {
              HIGH: '#ff716f',
              MEDIUM: '#ffb454',
              LOW: '#83a6ff',
            }[props.payload.severity] || '#ffb454'
            return (
              <g
                key={`${props.payload.date}-anomaly`}
                className="anomaly-chart-marker"
                role="button"
                tabIndex={0}
                aria-label={`${props.payload.severity} anomaly on ${props.payload.date}`}
                onClick={() => onAnomalyClick(props.payload)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') onAnomalyClick(props.payload)
                }}
              >
                <circle cx={props.cx} cy={props.cy} r={6} fill={color} stroke="#101720" strokeWidth={2} />
                <title>{`${props.payload.severity} anomaly · ${props.payload.date}`}</title>
              </g>
            )
          }}
        />
        <Line type="monotone" dataKey="movingAverage" stroke="#83a6ff" strokeWidth={1.7} dot={false} activeDot={{ r: 3 }} />
      </LineChart>
    </ResponsiveContainer>
  )
}

export default PriceChart
