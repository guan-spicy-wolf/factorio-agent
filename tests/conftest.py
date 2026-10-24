import pytest

from fgen.data.loader import load_seed
from fgen.data.schema import GameData


@pytest.fixture(scope="session")
def gd() -> GameData:
    return load_seed()


# ---- headless ----------------------------------------------------------------------
# Tests marked ``headless`` share one flat template save (created once per session) and
# get one server per test module; modules run one after another, so at most one server
# instance exists at any time.


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    from fgen.sim.server import binary_available

    if binary_available():
        return
    skip = pytest.mark.skip(reason="Factorio headless binary not found ($FACTORIO_DIR)")
    for item in items:
        if "headless" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def headless_workdir(tmp_path_factory: pytest.TempPathFactory):
    return tmp_path_factory.mktemp("headless")


@pytest.fixture(scope="module")
def server(headless_workdir):
    from fgen.sim.server import HeadlessServer

    srv = HeadlessServer(workdir=headless_workdir)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()
