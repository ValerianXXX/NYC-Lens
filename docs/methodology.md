# Methodology

## Catchments

Radial catchments are calculated after projection to EPSG:32118. Walking catchments use a locally versioned OpenStreetMap pedestrian network, bounded network reach, buffered traversable segments, and a concave hull. Network completeness and access tags vary, so the result is a modeled walking reach rather than observed travel time.

## Population and demographics

ACS 2020–2024 five-year estimates cover the 30-county CSA 408 footprint. Block-group data is preferred; tract-level tables are used where block-group estimates are not published. Counts use area weighting. Medians use an appropriate population weight. Published margins of error are retained, and derived ratios use conservative first-order propagation.

The 2020 Decennial Census is a point-in-time enumeration baseline and is never presented as a current population estimate.

## Jobs and businesses

LODES counts jobs and job flows, not unique people. Business locations are classified with deterministic taxonomy rules. Direct competitors, secondary competitors, substitutes, complements, closed locations, and unknown statuses remain separate. Density measures use resident and daytime-job denominators only when those denominators are available.

## Mobility

Subway, bus, regional rail, Citi Bike, traffic, pedestrian, and TLC measures retain their source granularity and limitations. Static schedules are not observed service. TLC trip data is stored only as area-level aggregates; individual trips and device traces are out of scope.

## Property and regulatory evidence

MapPLUTO, rolling sales, ACRIS, DOB, ECB/OATH, certificates of occupancy, and 311 aggregates are administrative or transaction records. They are screening evidence, not legal conclusions, appraisals, rent estimates, or proof of current site conditions.

## Health and environment

CDC PLACES, air-quality, flood, and evacuation values describe published areas or mapped overlaps. They must not be assigned to a specific person, household, tenant, employee, building occupant, or customer.

## Proxies and classification

Audience indexes and market signals are transparent proxies built from named component metrics. They do not estimate exact visitor shares or purchase probability. A composite site score is not produced because the project does not yet have reviewed real-project outcomes, a holdout set, or a model card.

## Confidence and missing data

ACS confidence reflects the margin-of-error-to-estimate relationship. Administrative records receive source-specific confidence. Model proxies remain conservative. Confidence describes evidence quality and method fit; it is not a probability that a business decision is correct. Missing values remain missing and are never silently converted to zero.
