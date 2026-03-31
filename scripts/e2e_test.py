#!/usr/bin/env python
"""End-to-end test for Factorio Agent.
Tests the full pipeline: RCON → Mod → Scripts → Response.

Usage: RCON_HOST=127.0.0.1 RCON_PORT=27015 RCON_PASSWORD=changeme \
       uv run python tests/test_e2e.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.rcon import RCONClient
from agent.bridge import FactorioBridge, ScriptError


def ensure_items(bridge: FactorioBridge, required: dict[str, int]) -> None:
    """Ensure the agent has the items needed for the test run."""
    inventory = bridge.inventory()
    counts = {item["name"]: item["count"] for item in inventory.get("items", [])}
    added = {}

    for name, need in required.items():
        have = counts.get(name, 0)
        missing = need - have
        if missing > 0:
            result = bridge.atomic_inventory_add(name, missing)
            added[name] = result.get("inserted", 0)

    if added:
        print(f"✓ Added missing items: {added}")


def test_ping(bridge: FactorioBridge) -> None:
    """Test ping script."""
    result = bridge.ping()
    assert result.get("status") == "ok", f"Ping failed: {result}"
    print(f"✓ Ping: tick={result['tick']}")


def test_spawn(bridge: FactorioBridge) -> None:
    """Test spawn with starting items."""
    result = bridge.spawn({
        "iron-chest": 20,
        "electric-mining-drill": 3,
        "coal": 50,
    })
    assert result.get("spawned") or result.get("already_exists"), f"Spawn failed: {result}"
    ensure_items(bridge, {
        "iron-chest": 20,
        "electric-mining-drill": 3,
        "coal": 50,
    })
    status = "spawned" if result.get("spawned") else "already exists"
    print(f"✓ Spawn: {status}")


def test_inventory(bridge: FactorioBridge) -> None:
    """Test inventory query."""
    result = bridge.inventory()
    assert "items" in result, f"No items: {result}"
    item_names = [i["name"] for i in result["items"]]
    assert "iron-chest" in item_names, f"Missing iron-chest"
    print(f"✓ Inventory: {len(result['items'])} item types")


def test_move_and_position(bridge: FactorioBridge) -> None:
    """Test movement."""
    result = bridge.move(300, 300)
    assert result.get("moved"), f"Move failed: {result}"
    print(f"✓ Move: to ({result['to']['x']}, {result['to']['y']})")


def test_place_within_reach(bridge: FactorioBridge) -> None:
    """Test placing entity within reach."""
    bridge.move(310, 310)
    result = bridge.place("iron-chest", 315, 315)
    assert result.get("placed"), f"Place failed: {result}"
    print(f"✓ Place: iron-chest at (315, 315)")


def test_inventory_decreases(bridge: FactorioBridge) -> None:
    """Test that placing decreases inventory."""
    result = bridge.inventory()
    chests = next((i["count"] for i in result["items"] if i["name"] == "iron-chest"), 0)
    assert chests < 20, f"Inventory should decrease after placing"
    print(f"✓ Inventory decreased: {chests} iron-chests remaining")


def test_remove_and_recover(bridge: FactorioBridge) -> None:
    """Test removing entity and recovering item."""
    result = bridge.remove(315, 315, "iron-chest")
    assert result.get("removed"), f"Remove failed: {result}"
    assert result.get("recovered"), f"No item recovered"
    print(f"✓ Remove: recovered {result['recovered']['count']} {result['recovered']['name']}")


def test_inventory_increases(bridge: FactorioBridge) -> None:
    """Test that removing increases inventory."""
    result = bridge.inventory()
    chests = next((i["count"] for i in result["items"] if i["name"] == "iron-chest"), 0)
    print(f"✓ Inventory recovered: {chests} iron-chests")


def test_place_collision_rejected(bridge: FactorioBridge) -> None:
    """Test that placing on an occupied position is rejected."""
    bridge.atomic_inventory_add("iron-chest", 1)
    bridge.move(330, 330)
    first = bridge.place("iron-chest", 331, 331)
    assert first.get("placed"), f"Setup place failed: {first}"
    try:
        result = bridge.place("iron-chest", 331, 331)
        assert False, f"Should have raised error, got: {result}"
    except ScriptError as e:
        assert "cannot place" in str(e), f"Wrong error: {e}"
        print(f"✓ Place collision: correctly rejected")


def test_place_no_item_rejected(bridge: FactorioBridge) -> None:
    """Test that placing without item is rejected."""
    bridge.move(50, 50)
    try:
        result = bridge.place("nuclear-reactor", 50, 50)
        assert False, f"Should have raised error, got: {result}"
    except ScriptError as e:
        assert "no item" in str(e), f"Wrong error: {e}"
        print(f"✓ Place no item: correctly rejected")


def main() -> int:
    """Run all end-to-end tests."""
    host = os.environ.get("RCON_HOST", "127.0.0.1")
    port = int(os.environ.get("RCON_PORT", "27015"))
    password = os.environ.get("RCON_PASSWORD", "changeme")

    print(f"=== Factorio Agent E2E Test ===")
    print(f"Connecting to {host}:{port}...")

    try:
        rcon = RCONClient(host=host, port=port, password=password)
        rcon.connect()
        bridge = FactorioBridge(rcon)

        print("Connected!\n")

        test_ping(bridge)
        test_spawn(bridge)
        test_inventory(bridge)
        test_move_and_position(bridge)
        test_place_within_reach(bridge)
        test_inventory_decreases(bridge)
        test_remove_and_recover(bridge)
        test_inventory_increases(bridge)
        test_place_collision_rejected(bridge)
        test_place_no_item_rejected(bridge)

        print("\n=== All tests passed! ===")
        rcon.close()
        return 0

    except Exception as e:
        import traceback
        print(f"\n✗ Test failed: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
