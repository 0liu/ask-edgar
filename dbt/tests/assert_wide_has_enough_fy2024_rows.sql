-- SPEC §4.3: fct_financials_wide holds at least 1,000 companies for fiscal year 2024 on
-- the full quarter. CI loads the 30-company fixture and lowers the bar through
-- ASK_EDGAR_MIN_FY2024_COMPANIES.
{% set minimum = env_var('ASK_EDGAR_MIN_FY2024_COMPANIES', '1000') | int %}
select count(*) as rows_fy2024
from {{ ref('fct_financials_wide') }}
where fiscal_year = 2024
having count(*) < {{ minimum }}
