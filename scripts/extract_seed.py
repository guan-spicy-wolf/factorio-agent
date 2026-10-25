"""Regenerate the seed data file from a real ``factorio --dump-data`` dump.

Usage: uv run python scripts/extract_seed.py <data-raw-dump.json> <factorio-version> [mods]

``mods`` defaults to the ``<dump>.mods`` stamp written by ``bin/headless.sh dump``.

Keeps the entity / recipe / item *names* and the ``_constants`` of the existing seed,
and for each prototype copies from the dump only the fields fgen reads (plus any key
already present in the seed), so the seed stays small while being real data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fgen.data.loader import SEED_FILE, fields_used

# Prototypes added on top of the names already in the seed (type -> names). Adding a
# name here and re-running the script is how the seed grows; never edit the JSON.
ADD: dict[str, list[str]] = {
    "mining-drill": ["big-mining-drill"],
    "resource": ["iron-ore", "copper-ore", "coal", "stone", "uranium-ore", "crude-oil"],
    "infinity-pipe": ["infinity-pipe"],  # phase 3a: fluid source fixture
    # phase 4b: science packs and their intermediates (direct_chain family)
    "recipe": ["automation-science-pack", "logistic-science-pack", "inserter", "transport-belt"],
}

SEED_PATH = Path(__file__).resolve().parents[1] / "src" / "fgen" / "data" / "seed" / SEED_FILE


def main() -> None:
    dump_path, version = sys.argv[1], sys.argv[2]
    if len(sys.argv) > 3:
        mods = sys.argv[3]
    else:
        stamp = Path(dump_path + ".mods")
        mods = stamp.read_text().strip().split(":", 1)[-1] if stamp.exists() else "unknown"
    dump = json.loads(Path(dump_path).read_text(encoding="utf-8"))
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    used = fields_used()

    out: dict = {
        "_provenance": (
            f"extracted from factorio {version} --dump-data (mods: {mods}; see "
            "bin/headless.sh dump) by scripts/extract_seed.py"
        ),
        "_version": version,
        "_constants": seed["_constants"],
    }
    missing = []
    wanted_names = {t: dict(v) for t, v in seed.items() if not t.startswith("_")}
    for proto_type, names in ADD.items():
        for name in names:
            wanted_names.setdefault(proto_type, {}).setdefault(name, {})
    for proto_type, protos in wanted_names.items():
        keys = {f.split(".")[0].split("[")[0] for f in used.get(proto_type, [])}
        out[proto_type] = {}
        for name, old in protos.items():
            raw = dump.get(proto_type, {}).get(name)
            if raw is None:
                missing.append(f"{proto_type}/{name}")
                continue
            wanted = keys | set(old)
            out[proto_type][name] = {k: raw[k] for k in raw if k in wanted}
    if missing:
        sys.exit(f"not in dump: {missing}")

    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    SEED_PATH.write_text(text, encoding="utf-8")
    n = sum(len(v) for k, v in out.items() if not k.startswith("_"))
    print(f"wrote {SEED_PATH} ({n} prototypes, version {version})")


if __name__ == "__main__":
    main()
