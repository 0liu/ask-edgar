{% macro metric_names() -%}
    {#- Distinct metrics from the metric_map seed, so fct_financials_wide pivots
        whatever the seed defines. Empty at parse time (no database yet). -#}
    {%- set metrics = [] -%}
    {%- if execute -%}
        {%- set results = run_query("select distinct metric from " ~ ref('metric_map') ~ " order by 1") -%}
        {%- for row in results.rows -%}
            {%- do metrics.append(row[0]) -%}
        {%- endfor -%}
    {%- endif -%}
    {{ return(metrics) }}
{%- endmacro %}
