{{ config(materialized='view') }}
-- depends_on: {{ ref('metric_map') }}

-- One row per (cik, fiscal_year) with one column per metric in the metric_map seed.
-- This is the relation the agent queries most of the time.

{%- set metrics = metric_names() %}

select
    f.cik,
    c.name as company_name,
    f.fiscal_year,
    max(f.period_end) as period_end
    {%- for metric in metrics %},
    max(f.value) filter (where f.metric = '{{ metric }}') as {{ metric }}
    {%- endfor %}
from {{ ref('fct_financials') }} as f
inner join {{ ref('dim_company') }} as c on c.cik = f.cik
group by f.cik, c.name, f.fiscal_year
