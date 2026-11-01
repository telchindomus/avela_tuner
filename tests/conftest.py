import pytest
from fake_radio import FakeRadio


@pytest.fixture
def radio() -> FakeRadio:
    return FakeRadio()
