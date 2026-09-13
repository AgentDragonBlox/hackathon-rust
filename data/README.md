# Public data provenance

Source: [Open Power System Data, Household Data, version 2020-04-15](https://data.open-power-system-data.org/household_data/2020-04-15/).
Primary measurements: CoSSMic, Konstanz, Germany. License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

This project transforms the source; it does not claim the meters measure
our campus. The JSON contains 168 hourly intervals from 2016-06-06 06:00 UTC
through 2016-06-13 06:00 UTC (exclusive end).

| Campus input | Source counter | Campus peak kW |
|---|---|---:|
| Academic | DE_KN_public1_grid_import | 240 |
| EV charging | DE_KN_industrial3_ev | 70 |
| Facility (factory agent) | DE_KN_industrial3_area_offices | 150 |
| Solar | DE_KN_industrial3_pv_roof | 165 |
| Hospital | No measured source; constant assumption | 380 |

We subtract consecutive cumulative kWh readings and divide by their one-hour
separation, assign the result to the interval start, then multiply each
channel by campus peak / source weekly peak. Missing, negative, non-finite
or non-hourly differences are rejected. We add no noise or interpolation;
source interpolation flags from both bounding readings are retained.

`source_average_kw` contains unscaled hourly averages. `campus_kw` contains
adapted inputs. Both differ from solved grid output, which also reflects
faults. The JSON retains download SHA-256, scaling peaks and attribution.
Regenerate with `python scripts/prepare_public_data.py` from the project root.
