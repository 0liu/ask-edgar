select
    adsh,
    cik,
    name as company_name,
    sic,
    countryinc as country_of_incorporation,
    stprinc as state_of_incorporation,
    fye as fiscal_year_end,
    afs as filer_status,
    form,
    period as period_end,
    fy as fiscal_year,
    fp as fiscal_period,
    filed,
    accepted,
    prevrpt as is_previous_report,
    detail as has_detail,
    nciks as cik_count
from {{ source('raw', 'sub') }}
