import pytest
from tools.browser import resolve_property


class TestResolveProperty:
    def test_exact_slug(self, aws_env):
        assert resolve_property("windmill") == "windmill"
        assert resolve_property("bellcrest") == "bellcrest"

    def test_partial_match(self, aws_env):
        assert resolve_property("wind") == "windmill"
        assert resolve_property("bell") == "bellcrest"

    def test_address_match(self, aws_env):
        assert resolve_property("Windmill Blvd") == "windmill"
        assert resolve_property("Bellcrest Rd") == "bellcrest"

    def test_unknown(self, aws_env):
        assert resolve_property("nonexistent") == ""

    def test_case_insensitive(self, aws_env):
        assert resolve_property("WINDMILL") == "windmill"
        assert resolve_property("Bellcrest") == "bellcrest"
