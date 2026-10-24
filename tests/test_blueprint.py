from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fgen.blueprint import codec
from fgen.blueprint.export import layout_to_blueprint
from fgen.blueprint.import_ import blueprint_to_layout
from fgen.data.schema import GameData
from fgen.geom.vec import Direction, Vec2
from fgen.layout.model import Layout, Placement

from .strategies import layouts

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("bp_*.txt"))

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text() | st.floats(allow_nan=False),
    lambda c: st.lists(c) | st.dictionaries(st.text(), c),
    max_leaves=20,
)


@given(st.dictionaries(st.text(), json_values))
def test_codec_roundtrip(obj: dict) -> None:
    s = codec.encode(obj)
    assert s.startswith("0")
    assert codec.decode(s) == obj


def test_decode_rejects_garbage() -> None:
    for bad in ["", "1abc", "0!!!!", "0" + "AAAA"]:
        with pytest.raises(ValueError):
            codec.decode(bad)


def test_version_packing(gd: GameData) -> None:
    v = codec.pack_version("2.0.28")
    assert v == (2 << 48) | (28 << 16)
    assert codec.unpack_version(v) == (2, 0, 28, 0)
    assert codec.unpack_version(codec.pack_version("1.1.110.3")) == (1, 1, 110, 3)
    major, minor, *_ = codec.unpack_version(codec.pack_version(gd.version))
    assert (major, minor) == tuple(int(p) for p in gd.version.split(".")[:2])
    with pytest.raises(ValueError):
        codec.pack_version("2.x")


@given(layouts(cardinal_only=False))
@settings(max_examples=150)
def test_layout_blueprint_roundtrip(gd: GameData, layout: Layout) -> None:
    bp = layout_to_blueprint(layout, gd, label="t")
    assert blueprint_to_layout(bp) == layout
    # also through the string form
    assert blueprint_to_layout(codec.decode(codec.encode(bp))) == layout


def test_export_shape(gd: GameData) -> None:
    lay = Layout([Placement("inserter", Vec2(0.5, 0.5), Direction.N, {"custom": 1})])
    bp = layout_to_blueprint(lay, gd)["blueprint"]
    e = bp["entities"][0]
    assert e["entity_number"] == 1 and e["direction"] == 0 and e["custom"] == 1
    assert bp["version"] == codec.pack_version(gd.version)
    with pytest.raises(ValueError):
        layout_to_blueprint(Layout([Placement("inserter", Vec2(0.5, 0.5), extra={"name": 1})]), gd)


def test_import_legacy_directions() -> None:
    bp = {
        "blueprint": {
            "version": codec.pack_version("1.1.110"),
            "entities": [
                {
                    "entity_number": 1,
                    "name": "inserter",
                    "position": {"x": 0.5, "y": 0.5},
                    "direction": 2,
                }
            ],
        }
    }
    assert blueprint_to_layout(bp).placements[0].direction == Direction.E
    with pytest.raises(ValueError):
        blueprint_to_layout({"blueprint_book": {}})


@pytest.mark.skipif(not FIXTURES, reason="no real blueprint fixtures available (see VERIFY.md)")
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_real_blueprints(path: Path) -> None:
    s = path.read_text().strip()
    obj = codec.decode(s)
    assert codec.unpack_version(obj["blueprint"]["version"])[0] == 2
    again = codec.decode(codec.encode(obj))
    assert again == obj
    layout = blueprint_to_layout(obj)
    assert len(layout.placements) == len(obj["blueprint"]["entities"])
    # entity-level fields survive import -> export of the entity list
    from fgen.blueprint.export import entity_dicts

    for orig, ours in zip(obj["blueprint"]["entities"], entity_dicts(layout), strict=True):
        assert {**orig, "direction": orig.get("direction", 0)} == ours
