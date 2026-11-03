-- One row per CIK, described by its most recent filing in the quarter.

with latest as (
    select
        *,
        row_number() over (
            partition by cik
            order by filed desc, accepted desc, adsh desc
        ) as rn
    from {{ ref('stg_submissions') }}
)

select
    l.cik,
    l.company_name as name,
    l.sic,
    s.sic_description,
    l.state_of_incorporation,
    l.fiscal_year_end,
    l.adsh as latest_adsh,
    l.form as latest_form,
    l.filed as latest_filed
from latest as l
left join {{ ref('sic_codes') }} as s on s.sic = l.sic
where l.rn = 1
