"""Original transport contracts describe macOS; run them on any test host.

Policy injection does not replace the actual OS for storage/pipe/native checks.
New Windows tests select their own policy and exercise their separate contract.
"""
import pytest

from openlocation_backend.host import HostPolicy

MAC_CONTRACTS = {
    "test_contract.py", "test_connection.py", "test_connect_flow.py",
    "test_discovery.py", "test_preparation.py", "test_setup.py", "test_wireless.py",
}


@pytest.fixture(autouse=True)
def original_transport_contract(request, monkeypatch):
    if request.path.name in MAC_CONTRACTS:
        from openlocation_backend import adapter, host, protocol, session
        mac = HostPolicy("macos", "arm64")
        for module in (adapter, host, protocol, session):
            monkeypatch.setattr(module, "HOST_POLICY", mac)
