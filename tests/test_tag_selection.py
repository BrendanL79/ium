"""Tests for safe tag selection.

Regression coverage for the 2026-05-24 incident where a transient codeberg
error made ium "update" forgejo/forgejo from 15.0.2 to 9.0.3: the base-tag
digest HEAD failed, find_matching_tag fell back to "latest matching tag",
and the lexicographic sort ranked "9.0.3" above "15.0.2".

Spec: docs/superpowers/specs/2026-06-04-tag-selection-safety-design.md
"""

import json
import re
from unittest.mock import patch, MagicMock

import pytest
import requests

from ium import DockerImageUpdater, _natural_sort_key


@pytest.fixture
def updater(tmp_path):
    config_file = tmp_path / "config.json"
    state_file = tmp_path / "state.json"
    config_file.write_text('{"images": []}')
    state_file.write_text("{}")
    return DockerImageUpdater(str(config_file), str(state_file))


def _mock_response(status_code=200, headers=None, json_data=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        # response= is required: _get_manifest_digest_head inspects
        # e.response.status_code to classify 404 vs other failures.
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} error", response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestNaturalSortKey:
    """_natural_sort_key orders digit runs numerically, text runs lexically."""

    def test_multi_digit_major_sorts_numerically(self):
        # The May 24 bug: "9.0.3" > "15.0.2" as strings.
        assert _natural_sort_key("9.0.3") < _natural_sort_key("15.0.2")

    def test_reverse_sort_puts_highest_major_first(self):
        tags = ["9.0.3", "15.0.2", "12.0.1", "15.0.1"]
        tags.sort(key=_natural_sort_key, reverse=True)
        assert tags == ["15.0.2", "15.0.1", "12.0.1", "9.0.3"]

    def test_different_digit_widths_within_component(self):
        # 3.10.0 is newer than 3.9.0; lexicographic gets this wrong.
        assert _natural_sort_key("3.9.0") < _natural_sort_key("3.10.0")

    def test_numeric_suffix_runs(self):
        # linuxserver -lsNNN suffixes cross digit-width boundaries too.
        assert (_natural_sort_key("6.1.1.10360-ls99")
                < _natural_sort_key("6.1.1.10360-ls301"))

    def test_v_prefix_tags(self):
        assert (_natural_sort_key("v9.8.0-ls399")
                < _natural_sort_key("v9.10.0-ls400"))

    def test_four_part_versions(self):
        assert (_natural_sort_key("6.1.1.9999-ls301")
                < _natural_sort_key("6.1.1.10360-ls301"))

    def test_equal_tags_equal_keys(self):
        assert _natural_sort_key("15.0.2") == _natural_sort_key("15.0.2")

    def test_heterogeneous_shapes_are_comparable(self):
        # Must never raise TypeError (bare int < str comparison).
        tags = ["latest", "15.0.2", "v2", "2025.11.1"]
        sorted(tags, key=_natural_sort_key)  # no exception
        assert _natural_sort_key("latest") != _natural_sort_key("15.0.2")
