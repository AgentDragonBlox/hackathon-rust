"""Download OPSD counters and build a reproducible, attributed campus replay."""
import csv
import hashlib
import io
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://data.open-power-system-data.org/household_data/2020-04-15/"
FILENAME = "household_data_60min_singleindex.csv"
CHANNELS = {
    "academic_kw": ("DE_KN_public1_grid_import", 240.0),
    "ev_kw": ("DE_KN_industrial3_ev", 70.0),
    "facility_kw": ("DE_KN_industrial3_area_offices", 150.0),
    "solar_kw": ("DE_KN_industrial3_pv_roof", 165.0),
}


def build_replay(raw: bytes) -> dict:
    start = datetime(2016, 6, 6, 6, tzinfo=timezone.utc)
    stop = start + timedelta(days=7)
    previous = None
    samples = []
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        timestamp = datetime.fromisoformat(row["utc_timestamp"].replace("Z", "+00:00"))
        if previous is not None:
            prior_time, prior = previous
            if start <= prior_time < stop:
                if timestamp - prior_time != timedelta(hours=1):
                    raise ValueError("Replay contains a missing hour")
                powers = {}
                for key, (column, _) in CHANNELS.items():
                    # Source values are cumulative kWh, not instantaneous kW.
                    value = float(row[column]) - float(prior[column])
                    if not math.isfinite(value) or value < 0:
                        raise ValueError(f"Invalid counter difference: {column} at {timestamp}")
                    powers[key] = value  # kWh / one hour = average kW
                markers = prior.get("interpolated", "") + ";" + row.get("interpolated", "")
                samples.append({
                    "timestamp": prior_time.isoformat().replace("+00:00", "Z"),
                    "source_average_kw": powers,
                    "source_interpolated_columns": [
                        column for column, _ in CHANNELS.values() if column.lower() in markers.lower()
                    ],
                })
        previous = timestamp, row
        if timestamp >= stop:
            break
    if len(samples) != 168:
        raise ValueError(f"Expected 168 hourly samples, found {len(samples)}")
    peaks = {key: max(sample["source_average_kw"][key] for sample in samples) for key in CHANNELS}
    for sample in samples:
        sample["campus_kw"] = {key: round(sample["source_average_kw"][key] / peaks[key] * peak, 6)
                               for key, (_, peak) in CHANNELS.items()}
        sample["campus_kw"]["hospital_kw"] = 380.0
    return {
        "name": "OPSD CoSSMic campus replay",
        "mode": "public_dataset_replay",
        "source_url": SOURCE,
        "download_url": SOURCE + FILENAME,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Open Power System Data. 2020. Data Package Household Data. Version 2020-04-15. Primary measurements: CoSSMic, Konstanz, Germany.",
        "sample_minutes": 60,
        "mapping": {key: {"source_column": column, "source_peak_kw": peaks[key], "campus_peak_kw": peak}
                    for key, (column, peak) in CHANNELS.items()},
        "transformation": "Consecutive cumulative kWh differences / 1 hour; each channel scaled to its stated campus peak over this week. No added noise or gap filling. OPSD interpolation flags retained.",
        "assumptions": "Hospital demand fixed at 380 kW; battery state, network topology, market prices and injected faults are simulated. Office demand represents the facility/factory agent. These are historical German profiles, not campus measurements or live telemetry.",
        "samples": samples,
    }


if __name__ == "__main__":
    cache = ROOT / "data" / "cache" / FILENAME
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(SOURCE + FILENAME, timeout=60) as response:
            cache.write_bytes(response.read())
    replay = build_replay(cache.read_bytes())
    output = ROOT / "data" / "opsd_replay.json"
    output.write_text(json.dumps(replay, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(replay['samples'])} samples to {output}")
