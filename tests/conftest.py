import pytest

from fgen.data.loader import load_seed
from fgen.data.schema import GameData


@pytest.fixture(scope="session")
def gd() -> GameData:
    return load_seed()
