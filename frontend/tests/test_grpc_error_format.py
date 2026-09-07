import grpc

from swarmgod_gui.core.grpc_client import format_rpc_error


class _FakeRpcError(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.FAILED_PRECONDITION

    def details(self):
        return "setup profile is telemetry-only"


def test_format_rpc_error_uses_code_and_details():
    assert format_rpc_error(_FakeRpcError()) == (
        "FAILED_PRECONDITION: setup profile is telemetry-only"
    )


def test_format_rpc_error_falls_back_for_regular_exception():
    assert format_rpc_error(ValueError("plain failure")) == "plain failure"
