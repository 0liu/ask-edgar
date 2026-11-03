-- SPEC §4.3: fct_financials_wide must hold at least 1,000 companies for fiscal year 2024
select count(*) as rows_fy2024
from {{ ref('fct_financials_wide') }}
where fiscal_year = 2024
having count(*) < 1000
