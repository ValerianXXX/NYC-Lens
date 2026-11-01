# Retained public response observations

These are actual small public responses observed on October 31, 2026
(America/New_York), not synthetic geometry and not a complete regional dataset.
FEMA attribution, public-data terms and source limitations remain applicable.

- [FEMA NFHL](https://www.fema.gov/flood-maps/national-flood-hazard-layer)
  supplies the layer metadata, five Lower Manhattan polygons with before/after
  IDs and counts, and a separate 355-ID diagnostic response.
- [FEMA layer 28](https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28?f=json)
  is the actual queried endpoint; each request's date, parameters, response size
  and hash appear in the retained observations.
- Census source files are described by `census-retrieval.json`; their large
  downloads and the resulting catalogs remain local and are not copied here.

`response-index.json` binds each `.response` file to the original bytes. Git
text conversion is disabled for those files. The two sets contain different
scopes; do not add their polygon counts into a coverage statistic.

`regional-attempts.json` preserves four incomplete regional observations,
including two upstream HTTP failures, an earlier harness timeout and a local
path-write failure. Personal local paths are replaced, request parameters are
represented by canonical hashes, and full original local receipt hashes remain.
Failed runs published zero FEMA polygons and retained the accepted county data.

From an installed source checkout, run:

```console
python scripts/verify_fema_observations.py
```

The replay uses no network, model or catalog. Passing it does not verify today's
upstream dataset, transactional stability, parcel insurance treatment, complete
trade-area analysis or a real business/location outcome.
