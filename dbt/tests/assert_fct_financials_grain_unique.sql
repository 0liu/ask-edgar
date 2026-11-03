-- fct_financials must have one row per (cik, fiscal_year, metric)
select cik, fiscal_year, metric, count(*) as n
from {{ ref('fct_financials') }}
group by 1, 2, 3
having count(*) > 1
