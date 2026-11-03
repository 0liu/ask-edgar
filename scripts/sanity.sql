-- Sanity checks after `make dbt` (PLAN Phase 2). Run with `make sanity`.

-- 1. Alphabet FY2024: revenue ~350e9, net income ~100e9
select company_name, fiscal_year, revenue, net_income, total_assets, eps_diluted
from analytics.fct_financials_wide
where company_name ilike 'ALPHABET INC%' and fiscal_year = 2024;

-- 2. Microsoft FY2024 net income: expected empty. Its fiscal year ends in June, so
--    the 10-K is in the 2024q3 data set, not 2025q1 (README limitations).
select company_name, fiscal_year, net_income
from analytics.fct_financials_wide
where company_name ilike 'MICROSOFT%';

-- 3. Filers per fiscal year (expect ~4,000 for 2024, a few dozen for 2023)
select fiscal_year, count(*) as filers
from analytics.fct_financials_wide
group by fiscal_year
order by fiscal_year;

-- 4. Metric coverage: companies with a value per metric, fiscal year 2024
select metric, count(*) as companies
from analytics.fct_financials
where fiscal_year = 2024
group by metric
order by companies desc;
