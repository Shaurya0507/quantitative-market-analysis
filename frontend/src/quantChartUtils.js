export function getDateAxisInterval(pointCount, maxLabels = 8) {
  return Math.max(0, Math.ceil(pointCount / maxLabels) - 1)
}

export function formatReturnPercent(value) {
  const percentage = Number(value) * 100
  return `${new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 2,
  }).format(Object.is(percentage, -0) ? 0 : percentage)}%`
}
