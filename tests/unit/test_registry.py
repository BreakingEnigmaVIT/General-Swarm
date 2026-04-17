"""Unit tests for core.registry."""

import pytest
from core.exceptions import AlreadyRegisteredError, NotRegisteredError
from core.registry import Registry


class FakeThing:
    def __init__(self, name: str):
        self.name = name


@pytest.fixture
def reg():
    return Registry[FakeThing]("thing")


def test_register_and_lookup(reg):
    thing = FakeThing("foo")
    reg.register("foo", thing)
    assert reg.lookup("foo") is thing


def test_lookup_missing_raises(reg):
    with pytest.raises(NotRegisteredError):
        reg.lookup("missing")


def test_duplicate_register_raises(reg):
    reg.register("foo", FakeThing("foo"))
    with pytest.raises(AlreadyRegisteredError):
        reg.register("foo", FakeThing("foo2"))


def test_overwrite_allowed(reg):
    t1 = FakeThing("v1")
    t2 = FakeThing("v2")
    reg.register("x", t1)
    reg.register("x", t2, overwrite=True)
    assert reg.lookup("x") is t2


def test_list(reg):
    reg.register("b", FakeThing("b"))
    reg.register("a", FakeThing("a"))
    assert reg.list() == ["a", "b"]  # sorted


def test_validate(reg):
    reg.register("present", FakeThing("present"))
    assert reg.validate("present") is True
    assert reg.validate("absent") is False


def test_unregister(reg):
    reg.register("x", FakeThing("x"))
    reg.unregister("x")
    assert reg.validate("x") is False


def test_unregister_missing_raises(reg):
    with pytest.raises(NotRegisteredError):
        reg.unregister("ghost")
