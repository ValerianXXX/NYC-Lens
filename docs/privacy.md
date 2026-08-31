# Privacy boundary

NYC Lens is designed for area-level commercial research, not individual profiling.

## Allowed

- Public address standardization and parcel matching.
- Area-level demographic, workforce, mobility, health, environmental, property, and administrative statistics.
- Aggregated counts, survey estimates, modeled proxies, and source-linked evidence.
- Operator-supplied commercial aggregates only after license and privacy validation.

## Prohibited

- Names, phone numbers, email addresses, device identifiers, or personal movement traces.
- Inferring that an area's income, language, ethnicity, health, or household averages describe a specific person or address occupant.
- Re-identification, household targeting, protected-class targeting, or sensitive individual scoring.
- Presenting a modeled visitor or audience proxy as an observed person count.
- Contacting, purchasing from, logging into, or scraping a commercial provider without explicit operator authority and reviewed terms.

## Retention and exports

The working database remains local. Exports contain site-level and area-level results, sources, years, confidence, limitations, and missing-data notes. Credentials and populated `.env` files must never be committed or included in reports.

## Scoring gate

Automated composite scoring remains `BLOCKED_CALIBRATION` until lawful real-project outcomes, a defined target, temporal holdout data, bias review, and a model card have been approved.
