select
    tag,
    version,
    custom as is_custom,
    abstract as is_abstract,
    datatype,
    iord as balance_type,      -- I = instant (balance sheet), D = duration (flow)
    crdr as natural_balance,   -- C = credit, D = debit
    tlabel as label,
    doc as definition
from {{ source('raw', 'tag') }}
