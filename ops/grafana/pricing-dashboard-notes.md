# Grafana Dashboard Panels

Create dashboard panels from these Prometheus metrics:

- Quote volume by service: `sum by (service,status)(rate(pricing_quote_requests_total[5m]))`
- Quote latency p95: `histogram_quantile(0.95, sum(rate(pricing_quote_duration_seconds_bucket[5m])) by (le,service))`
- Quote value by service: `histogram_quantile(0.50, sum(rate(pricing_quote_value_usd_bucket[1h])) by (le,service))`
- Missing inputs by field: `sum by (service,field)(increase(pricing_missing_inputs_total[24h]))`
- Human review reasons: `sum by (service,reason)(increase(pricing_human_review_total[24h]))`
- Latest range width: `pricing_quote_range_width`
