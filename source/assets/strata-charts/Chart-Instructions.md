When the user asks for a data chart, emit a fenced code block with language
`strata-chart`. Its body must be a single valid JSON object with:

- `title` — a short title in the user's language.
- `categories` — 1 to 24 labels.
- `series` — 1 to 6 metrics. Each metric has a `label`, an optional `unit`,
  and `values` with one finite JSON number per category, in the same order.
  Multiple metrics appear as switches in the same chart.

Example with illustrative values:

```strata-chart
{"title":"推理表现（示例数据）","categories":["IQ2_XS","IQ3_XXS"],"series":[{"label":"解码速度","unit":"tok/s","values":[24,18]},{"label":"显存","unit":"GiB","values":[15,19]}]}
```

Use values from the user's data or verified tool results. Mark illustrative
values explicitly. For a reproducible rendering test, use the supplied values
and put the completed chart block directly in the response.
