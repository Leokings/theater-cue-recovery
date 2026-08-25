import os

import pytest
from genlayer_py import create_account
from tests.windows_compat import install


install()


@pytest.fixture(autouse=True)
def hardened_direct_mode(request):
    if "direct_vm" not in request.fixturenames:
        yield
        return
    direct_vm = request.getfixturevalue("direct_vm")
    direct_vm.check_pickling = True
    direct_vm.strict_mocks = True
    yield


@pytest.fixture(scope="session")
def default_account():
    private_key = os.getenv("GENLAYER_PRIVATE_KEY")
    if not private_key:
        pytest.fail("GENLAYER_PRIVATE_KEY is required for StudioNet tests")
    return create_account(private_key)

