select
    adsh,
    report,
    line,
    stmt as statement,         -- BS, IS, CF, EQ, CI, CP, UN
    inpth as is_parenthetical,
    rfile as rendered_file_type,
    tag,
    version,
    plabel as label,
    negating as is_negating
from {{ source('raw', 'pre') }}
