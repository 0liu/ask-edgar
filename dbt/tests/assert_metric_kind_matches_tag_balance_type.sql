-- A balance metric must map to instant tags (iord = I); flow and per-share metrics
-- to duration tags (iord = D). Catches a wrong `kind` in metric_map.
select m.metric, m.tag, m.kind, t.balance_type
from {{ ref('metric_map') }} as m
inner join {{ ref('stg_tags') }} as t
    on t.tag = m.tag and t.version like 'us-gaap/%'
where (m.kind = 'balance') <> (t.balance_type = 'I')
