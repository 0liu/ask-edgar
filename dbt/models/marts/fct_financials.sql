-- One row per (cik, fiscal_year, metric): the consolidated annual figure from the
-- company's 10-K, with the tag and filing it came from. Rules from SPEC §4.3:
--   * 10-K and 10-K/A only
--   * consolidated only: no segment axis, no co-registrant
--   * balance metrics at the balance-sheet date (qtrs = 0); flow and per-share
--     metrics for the full year ending there (qtrs = 4)
--   * per metric, the latest filing wins (a 10-K/A restates), then the lowest
--     priority tag present in it, then a USD / shares unit over any other

with filings as (

    select adsh, cik, company_name, form, fiscal_year, fiscal_period, period_end, filed, accepted
    from {{ ref('stg_submissions') }}
    where form in ('10-K', '10-K/A')
      and fiscal_year is not null
      and period_end is not null

),

candidates as (

    select
        f.cik,
        f.company_name,
        f.adsh,
        f.form,
        f.fiscal_year,
        f.fiscal_period,
        f.period_end,
        f.filed,
        f.accepted,
        m.metric,
        m.priority,
        n.tag as source_tag,
        n.version,
        n.qtrs,
        n.uom,
        n.value
    from filings as f
    inner join {{ ref('stg_numbers') }} as n on n.adsh = f.adsh
    inner join {{ ref('metric_map') }} as m on m.tag = n.tag
    where n.segments is null
      and n.coreg is null
      and n.value is not null
      and n.period_end = f.period_end
      and n.qtrs = case when m.kind = 'balance' then 0 else 4 end

),

ranked as (

    select
        *,
        row_number() over (
            partition by cik, fiscal_year, metric
            order by
                filed desc,
                accepted desc,
                priority,
                case when uom in ('USD', 'USD/shares', 'shares', 'pure') then 0 else 1 end,
                uom,
                version desc,
                adsh desc
        ) as rn
    from candidates

)

select
    cik,
    company_name,
    adsh,
    form,
    fiscal_year,
    fiscal_period,
    period_end,
    metric,
    value,
    uom,
    source_tag,
    qtrs
from ranked
where rn = 1
