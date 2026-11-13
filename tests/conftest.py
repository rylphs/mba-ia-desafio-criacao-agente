import pytest

from aurora import db


@pytest.fixture(autouse=True)
def var_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_VAR_DIR", str(tmp_path))
    db.inicializar_se_preciso()
    return tmp_path


class FakeToolContext:
    def __init__(self, apartamento, function_call_id="call-1", tool_confirmation=None):
        self.state = {"apartamento": apartamento}
        self.function_call_id = function_call_id
        self.tool_confirmation = tool_confirmation
        self.pedidos = []

    def request_confirmation(self, *, hint=None, payload=None):
        self.pedidos.append({"hint": hint, "payload": payload})
