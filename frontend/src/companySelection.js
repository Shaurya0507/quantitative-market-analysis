export function uniqueCompanies(companies) {
  const byTicker = new Map()
  companies.forEach((company) => {
    if (company?.ticker && !byTicker.has(company.ticker)) {
      byTicker.set(company.ticker, company)
    }
  })
  return [...byTicker.values()]
}

export function suggestedCompanies(stocks, directoryCompanies, limit = 8) {
  const cached = stocks.map((stock) => ({ ...stock, is_loaded: true }))
  const directory = directoryCompanies.map((company) => ({
    ...company,
    is_loaded: Boolean(company.historical_data_available || company.is_loaded),
  }))
  return uniqueCompanies([...cached, ...directory]).slice(0, limit)
}

export function companyMatchesQuery(company, query) {
  const normalized = query.trim().toLocaleLowerCase()
  return Boolean(normalized) && [
    company.ticker,
    company.company_name,
  ].some((value) => value?.toLocaleLowerCase().includes(normalized))
}
