import pytest

dbus = pytest.importorskip("dbus")


def test_empty_value_is_typed_byte_array():
    value = dbus.Array([], signature="y")
    assert isinstance(value, dbus.Array)
    assert value.signature == dbus.Signature("y")
