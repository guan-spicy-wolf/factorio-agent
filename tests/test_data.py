import json
from pathlib import Path

import pytest

from fgen.data.loader import _ENTITY_FIELDS, fields_used, load_dump, load_seed
from fgen.data.schema import GameData, parse_energy

REQUIRED_ENTITIES = [
    "transport-belt",
    "fast-transport-belt",
    "express-transport-belt",
    "underground-belt",
    "fast-underground-belt",
    "express-underground-belt",
    "splitter",
    "fast-splitter",
    "express-splitter",
    "burner-inserter",
    "inserter",
    "fast-inserter",
    "long-handed-inserter",
    "bulk-inserter",
    "small-electric-pole",
    "medium-electric-pole",
    "big-electric-pole",
    "substation",
    "stone-furnace",
    "steel-furnace",
    "electric-furnace",
    "assembling-machine-1",
    "assembling-machine-2",
    "assembling-machine-3",
    "burner-mining-drill",
    "electric-mining-drill",
    "pipe",
    "pipe-to-ground",
    "offshore-pump",
    "pumpjack",
    "storage-tank",
    "wooden-chest",
    "iron-chest",
    "steel-chest",
]
REQUIRED_RECIPES = [
    "iron-plate",
    "copper-plate",
    "stone-brick",
    "steel-plate",
    "copper-cable",
    "electronic-circuit",
    "iron-gear-wheel",
]


def test_seed_loads(gd: GameData) -> None:
    assert gd.source == "seed"
    assert gd.version.startswith("2.")
    for name in REQUIRED_ENTITIES:
        assert name in gd.entities, name
    for name in REQUIRED_RECIPES:
        assert name in gd.recipes, name


def test_seed_has_provenance() -> None:
    from fgen.data.loader import _seed_raw

    raw = _seed_raw()
    assert "--dump-data" in raw["_provenance"]
    assert raw["_version"].startswith("2.")
    assert "mods:" in raw["_provenance"]


def test_seed_type_specific_fields_present(gd: GameData) -> None:
    for e in gd.entities.values():
        x1, y1 = e.collision_box[0]
        x2, y2 = e.collision_box[1]
        assert x1 < x2 and y1 < y2
        if e.type == "inserter":
            assert e.pickup_position is not None and e.insert_position is not None
            assert e.rotation_speed and e.extension_speed
        if e.type == "electric-pole":
            assert e.supply_area_distance and e.maximum_wire_distance
        if e.type in ("transport-belt", "underground-belt", "splitter"):
            assert e.speed
        if e.type == "underground-belt":
            assert e.max_distance
        if e.type in ("furnace", "assembling-machine"):
            assert e.crafting_speed and e.crafting_categories and e.energy_source
        if e.type == "container":
            assert e.inventory_size
        if e.type in ("pipe", "pipe-to-ground", "offshore-pump", "storage-tank"):
            assert e.fluid_box is not None and e.fluid_box.pipe_connections
    assert gd.entity("pipe-to-ground").max_underground_distance
    assert gd.entity("pumpjack").output_fluid_box is not None


def test_recipes_parsed(gd: GameData) -> None:
    r = gd.recipe("electronic-circuit")
    assert {n for n, _ in r.ingredients} == {"iron-plate", "copper-cable"}
    assert r.results[0][0] == "electronic-circuit"
    assert r.energy_required > 0


def test_parse_energy() -> None:
    assert parse_energy("90kW") == 90_000
    assert parse_energy("4MJ") == 4_000_000
    assert parse_energy("5.5kJ") == 5_500
    assert parse_energy(12) == 12.0
    with pytest.raises(ValueError):
        parse_energy("lots")


# A hand-built fragment shaped like data-raw-dump.json, deliberately using the
# alternative encodings a real dump may contain (dict vectors, left_top boxes,
# string directions, short-form ingredients, unknown extra fields).
DUMP_FRAGMENT = {
    "inserter": {
        "test-inserter": {
            "type": "inserter",
            "name": "test-inserter",
            "collision_box": {"left_top": {"x": -0.15, "y": -0.15}, "right_bottom": [0.15, 0.15]},
            "pickup_position": {"x": 0, "y": -1},
            "insert_position": [0, 1.2],
            "rotation_speed": 0.5,
            "extension_speed": 0.25,
            "energy_per_movement": "1kJ",
            "energy_source": {"type": "electric", "usage_priority": "secondary-input"},
            "icon": "__base__/graphics/whatever.png",
        }
    },
    "electric-pole": {
        "test-pole": {
            "type": "electric-pole",
            "name": "test-pole",
            "collision_box": [[-0.15, -0.15], [0.15, 0.15]],
            "supply_area_distance": 1.5,
            "maximum_wire_distance": 3,
        }
    },
    "pipe-to-ground": {
        "test-ptg": {
            "type": "pipe-to-ground",
            "name": "test-ptg",
            "collision_box": [[-0.3, -0.3], [0.3, 0.3]],
            "fluid_box": {
                "volume": 100,
                "pipe_connections": [
                    {"direction": "north", "position": [0, 0]},
                    {
                        "direction": 8,
                        "position": [0, 0],
                        "connection_type": "underground",
                        "max_underground_distance": 3,
                    },
                ],
            },
        }
    },
    "furnace": {
        "test-furnace": {
            "type": "furnace",
            "name": "test-furnace",
            "collision_box": [[-0.7, -0.7], [0.7, 0.7]],
            "crafting_speed": 3,
            "crafting_categories": ["smelting"],
            "energy_usage": "1MW",
            "energy_source": {"type": "burner", "fuel_category": "chemical"},
        }
    },
    "lamp": {"ignored-lamp": {"type": "lamp", "name": "ignored-lamp"}},
    "item": {
        "test-fuel": {"type": "item", "name": "test-fuel", "stack_size": 7, "fuel_value": "1MJ"}
    },
    "recipe": {
        "new-style": {
            "type": "recipe",
            "name": "new-style",
            "category": "smelting",
            "energy_required": 2,
            "ingredients": [{"type": "item", "name": "a", "amount": 3}],
            "results": [{"type": "item", "name": "b", "amount": 2}],
        },
        "old-style": {
            "type": "recipe",
            "name": "old-style",
            "ingredients": [["a", 1]],
            "result": "c",
        },
    },
}


def test_load_dump_fragment(tmp_path: Path) -> None:
    p = tmp_path / "data-raw-dump.json"
    p.write_text(json.dumps(DUMP_FRAGMENT))
    gd = load_dump(p, version="2.0.99")
    assert gd.source == "dump" and gd.version == "2.0.99"
    ins = gd.entity("test-inserter")
    assert ins.collision_box == ((-0.15, -0.15), (0.15, 0.15))
    assert ins.pickup_position == (0.0, -1.0) and ins.insert_position == (0.0, 1.2)
    assert ins.rotation_speed == 0.5 and ins.is_electric
    pole = gd.entity("test-pole")
    assert pole.supply_area_distance == 1.5 and pole.maximum_wire_distance == 3
    ptg = gd.entity("test-ptg")
    assert ptg.max_underground_distance == 3
    assert [c.direction for c in ptg.fluid_box.pipe_connections] == [0, 8]
    assert ptg.fluid_box.pipe_connections[1].connection_type == "underground"
    fur = gd.entity("test-furnace")
    assert fur.is_burner and fur.energy_source.fuel_categories == ("chemical",)
    assert "ignored-lamp" not in gd.entities
    assert gd.items["test-fuel"].stack_size == 7
    assert gd.recipe("new-style").results == (("b", 2.0),)
    old = gd.recipe("old-style")
    assert old.ingredients == (("a", 1.0),) and old.results == (("c", 1.0),)
    assert old.categories == ("crafting",)
    assert old.energy_required == gd.constants["recipe_default_energy_required"]
    # constants fall back to the seed file
    assert gd.constants == load_seed().constants


def test_fields_used_covers_all_entity_types() -> None:
    fu = fields_used()
    for t in _ENTITY_FIELDS:
        assert "collision_box" in fu[t]
    assert "supply_area_distance" in fu["electric-pole"]
    assert "pickup_position" in fu["inserter"]
    assert "recipe" in fu and "item" in fu


def test_seed_resources_and_big_drill(gd: GameData) -> None:
    for name in ("iron-ore", "copper-ore", "coal", "stone"):
        r = gd.resource(name)
        assert r.category == "basic-solid" and not r.infinite and r.required_fluid is None
        assert r.mining_time > 0 and r.product == name
    oil = gd.resource("crude-oil")
    assert oil.infinite and oil.category == "basic-fluid"
    assert gd.resource("uranium-ore").required_fluid
    emd = gd.entity("electric-mining-drill")
    assert emd.resource_categories == ("basic-solid",) and not emd.drops_full_belt_stacks
    big = gd.entity("big-mining-drill")
    assert big.type == "mining-drill" and big.drops_full_belt_stacks
    assert big.mining_speed and big.resource_searching_radius and big.vector_to_place_result
    assert "basic-solid" in big.resource_categories
    assert "resource" in fields_used()


def test_load_dump_resource_forms(tmp_path: Path) -> None:
    frag = {
        "resource": {
            "short": {"minable": {"mining_time": 2, "result": "short", "count": 3}},
            "long": {
                "category": "basic-fluid",
                "infinite": True,
                "minable": {
                    "mining_time": 1,
                    "results": [{"type": "fluid", "name": "goo", "amount": 10}],
                },
            },
        }
    }
    p = tmp_path / "d.json"
    p.write_text(json.dumps(frag))
    gd = load_dump(p, version="2.1.0")
    s, lg = gd.resource("short"), gd.resource("long")
    assert s.results == (("short", 3.0),) and s.category == "basic-solid" and s.mining_time == 2
    assert lg.infinite and lg.results == (("goo", 10.0),) and lg.category == "basic-fluid"


def test_phase5a_fluid_prototypes(gd: GameData) -> None:
    """Chemical plant ``fluid_boxes`` (two inputs, two outputs), fluids with their
    default temperature, the pipeline extent constant (all from the 2.1.23 dump)."""
    cp = gd.entity("chemical-plant")
    kinds = [fb.production_type for fb in cp.fluid_boxes]
    assert kinds.count("input") == 2 and kinds.count("output") == 2
    assert cp.use_mirroring and not cp.fluid_boxes_off_when_no_fluid_recipe
    for fb in cp.fluid_boxes:
        (c,) = fb.pipe_connections
        assert c.flow_direction == fb.production_type
    assert {"petroleum-gas", "water", "sulfuric-acid"} <= set(gd.fluids)
    assert all(f.default_temperature > 0 for f in gd.fluids.values())
    assert gd.is_fluid("water") and not gd.is_fluid("coal")
    assert gd.constant("pipeline_extent") > 0
    assert {"plastic-bar", "sulfuric-acid"} <= set(gd.recipes)
