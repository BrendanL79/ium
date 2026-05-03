"""Tests for _cleanup_old_images.

Regression coverage for the bug where the just-pulled image carries TWO
local tags (the version-specific tag and the moving :latest tag — see
ium.py:1427 / 1494, which pulls both image:base_tag and image:matching_tag),
and the cleanup logic counted *tag rows* rather than *distinct image IDs*.
With keep_versions=3, the two tags on the new image consumed two of the
three "keep" slots, leaving only ONE old image preserved instead of the
intended three.

The fix dedupes by image ID before applying keep_versions.
"""

from unittest.mock import patch
import pytest

from ium import DockerImageUpdater


@pytest.fixture
def updater(tmp_path):
    config_file = tmp_path / "config.json"
    state_file = tmp_path / "state.json"
    config_file.write_text('{"images": []}')
    state_file.write_text("{}")
    return DockerImageUpdater(str(config_file), str(state_file))


def _img(image_id: str, tags: list[str], created: int) -> dict:
    """Build a Docker /images/json entry."""
    return {
        "Id": f"sha256:{image_id}",
        "RepoTags": tags,
        "Created": created,
    }


class TestCleanupCountsDistinctImageIDs:
    """Cleanup must keep `keep_versions` distinct image IDs, not tag rows."""

    def test_new_image_with_two_tags_does_not_eat_keep_slots(self, updater):
        """Reproduces the NAS bug: the newest image has both :version and
        :latest pointing to it (since ium pulls both), and four real distinct
        image versions exist on disk. keep_versions=3 must preserve THREE
        distinct images, not let :latest eat a slot."""
        api_images = [
            # Newest — same image ID, two tags (the bug trigger)
            _img("aaa" * 21,
                 ["linuxserver/radarr:6.1.1.10360-ls301",
                  "linuxserver/radarr:latest"],
                 created=5000),
            _img("bbb" * 21, ["linuxserver/radarr:6.1.1.10360-ls300"], created=4000),
            _img("ccc" * 21, ["linuxserver/radarr:6.1.1.10360-ls299"], created=3000),
            _img("ddd" * 21, ["linuxserver/radarr:6.1.1.10360-ls298"], created=2000),
        ]
        removed = []
        with patch.object(updater.docker, "list_images", return_value=api_images), \
             patch.object(updater.docker, "remove_image",
                          side_effect=lambda ref: removed.append(ref) or True):
            updater._cleanup_old_images("linuxserver/radarr", keep_versions=3)

        # Only ls298 (the 4th distinct image) should be removed.
        # Neither :latest nor any of the top-3 distinct images should be touched.
        assert removed == ["linuxserver/radarr:6.1.1.10360-ls298"]

    def test_simple_case_no_extra_tags(self, updater):
        """Sanity check: with one tag per image and 4 images, keep_versions=3
        removes the oldest one."""
        api_images = [
            _img("aaa" * 21, ["linuxserver/radarr:ls004"], created=4000),
            _img("bbb" * 21, ["linuxserver/radarr:ls003"], created=3000),
            _img("ccc" * 21, ["linuxserver/radarr:ls002"], created=2000),
            _img("ddd" * 21, ["linuxserver/radarr:ls001"], created=1000),
        ]
        removed = []
        with patch.object(updater.docker, "list_images", return_value=api_images), \
             patch.object(updater.docker, "remove_image",
                          side_effect=lambda ref: removed.append(ref) or True):
            updater._cleanup_old_images("linuxserver/radarr", keep_versions=3)
        assert removed == ["linuxserver/radarr:ls001"]

    def test_fewer_images_than_keep_removes_nothing(self, updater):
        api_images = [
            _img("aaa" * 21, ["linuxserver/radarr:ls002"], created=2000),
            _img("bbb" * 21, ["linuxserver/radarr:ls001"], created=1000),
        ]
        removed = []
        with patch.object(updater.docker, "list_images", return_value=api_images), \
             patch.object(updater.docker, "remove_image",
                          side_effect=lambda ref: removed.append(ref) or True):
            updater._cleanup_old_images("linuxserver/radarr", keep_versions=3)
        assert removed == []

    def test_multi_tagged_old_image_all_tags_dropped(self, updater):
        """If an OLD image (one we've decided to remove) has multiple tags,
        every tag must be dropped — otherwise the image stays on disk."""
        api_images = [
            _img("aaa" * 21, ["linuxserver/radarr:ls004"], created=4000),
            _img("bbb" * 21, ["linuxserver/radarr:ls003"], created=3000),
            _img("ccc" * 21, ["linuxserver/radarr:ls002"], created=2000),
            # Old image with two tags — both must be dropped to actually free it
            _img("ddd" * 21,
                 ["linuxserver/radarr:ls001",
                  "linuxserver/radarr:ls001-stale-alias"],
                 created=1000),
        ]
        removed = []
        with patch.object(updater.docker, "list_images", return_value=api_images), \
             patch.object(updater.docker, "remove_image",
                          side_effect=lambda ref: removed.append(ref) or True):
            updater._cleanup_old_images("linuxserver/radarr", keep_versions=3)
        assert sorted(removed) == sorted([
            "linuxserver/radarr:ls001",
            "linuxserver/radarr:ls001-stale-alias",
        ])

    def test_dangling_none_tags_skipped(self, updater):
        """Images with only <none>:<none> RepoTags (dangling) shouldn't crash
        the cleanup, and shouldn't count as a 'kept' version."""
        api_images = [
            _img("aaa" * 21, ["linuxserver/radarr:ls003"], created=3000),
            _img("bbb" * 21, ["linuxserver/radarr:ls002"], created=2000),
            _img("ccc" * 21, ["<none>:<none>"], created=1500),
            _img("ddd" * 21, ["linuxserver/radarr:ls001"], created=1000),
        ]
        removed = []
        with patch.object(updater.docker, "list_images", return_value=api_images), \
             patch.object(updater.docker, "remove_image",
                          side_effect=lambda ref: removed.append(ref) or True):
            updater._cleanup_old_images("linuxserver/radarr", keep_versions=3)
        # Three real tagged versions should be kept (ls003, ls002, ls001),
        # and the dangling one is ignored (no tag we could pass to docker rm).
        assert removed == []
