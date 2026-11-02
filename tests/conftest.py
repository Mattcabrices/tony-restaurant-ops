import copy
import pytest
from app.storage import DB


class MemoryVault:
    def __init__(self, data=None):
        self.data = data or {}

    def read(self):
        return copy.deepcopy(self.data)

    def update(self, **values):
        self.data.update(copy.deepcopy(values))


@pytest.fixture
def vault():
    return MemoryVault()


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path)
