select
    adsh,
    tag,
    version,
    ddate as period_end,
    qtrs,
    uom,
    segments,
    coreg,
    value,
    footnote
from {{ source('raw', 'num') }}
