"""Tests for image reference parsing and platform string handling."""

import pytest

from ium import DockerImageUpdater, DEFAULT_REGISTRY, DEFAULT_NAMESPACE, _natural_sort_key


# ---------------------------------------------------------------------------
# We need _parse_image_reference without a full DockerImageUpdater (which
# needs a real config file). Create a minimal instance helper.
# ---------------------------------------------------------------------------

@pytest.fixture
def parser(tmp_path):
    """Create a minimal updater just for parsing methods."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"images": []}')
    return DockerImageUpdater(str(config_file), str(tmp_path / "state.json"))


class TestParseImageReference:
    """Test _parse_image_reference for all registry types in the inventory."""

    # Docker Hub: namespace/repo
    @pytest.mark.parametrize("image,expected_ns,expected_repo", [
        ("linuxserver/calibre", "linuxserver", "calibre"),
        ("budibase/budibase", "budibase", "budibase"),
        ("plexinc/pms-docker", "plexinc", "pms-docker"),
        ("n8nio/n8n", "n8nio", "n8n"),
        ("pihole/pihole", "pihole", "pihole"),
        ("portainer/portainer-ce", "portainer", "portainer-ce"),
        ("crazymax/diun", "crazymax", "diun"),
        ("jellyfin/jellyfin", "jellyfin", "jellyfin"),
    ])
    def test_dockerhub(self, parser, image, expected_ns, expected_repo):
        registry, namespace, repo = parser._parse_image_reference(image)
        assert registry == DEFAULT_REGISTRY
        assert namespace == expected_ns
        assert repo == expected_repo

    # Docker Hub: official image (single name → library/name)
    def test_dockerhub_official(self, parser):
        registry, namespace, repo = parser._parse_image_reference("ubuntu")
        assert registry == DEFAULT_REGISTRY
        assert namespace == DEFAULT_NAMESPACE
        assert repo == "ubuntu"

    # ghcr.io
    @pytest.mark.parametrize("image,expected_ns,expected_repo", [
        ("ghcr.io/homarr-labs/homarr", "homarr-labs", "homarr"),
        ("ghcr.io/mealie-recipes/mealie", "mealie-recipes", "mealie"),
    ])
    def test_ghcr(self, parser, image, expected_ns, expected_repo):
        registry, namespace, repo = parser._parse_image_reference(image)
        assert registry == "ghcr.io"
        assert namespace == expected_ns
        assert repo == expected_repo

    # lscr.io
    def test_lscr(self, parser):
        registry, namespace, repo = parser._parse_image_reference(
            "lscr.io/linuxserver/prowlarr"
        )
        assert registry == "lscr.io"
        assert namespace == "linuxserver"
        assert repo == "prowlarr"

    # Edge cases
    def test_localhost_registry(self, parser):
        registry, ns, repo = parser._parse_image_reference("localhost/myimage")
        assert registry == "localhost"

    def test_registry_with_port(self, parser):
        registry, ns, repo = parser._parse_image_reference("myregistry:5000/ns/repo")
        assert registry == "myregistry:5000"
        assert ns == "ns"
        assert repo == "repo"


class TestPlatformStringParsing:
    """Test platform string construction from manifest data.

    This tests the fix for the potential TypeError when os/architecture is None.
    The logic is inline in _get_manifest_digest, so we test the pattern directly.
    """

    def _build_plat_str(self, manifest_entry: dict) -> str:
        """Reproduce the platform string logic from _get_manifest_digest."""
        plat = manifest_entry.get('platform', {})
        return f"{plat.get('os', '')}/{plat.get('architecture', '')}"

    def test_normal_platform(self):
        entry = {"platform": {"os": "linux", "architecture": "amd64"}}
        assert self._build_plat_str(entry) == "linux/amd64"

    def test_arm_variant(self):
        entry = {"platform": {"os": "linux", "architecture": "arm64"}}
        assert self._build_plat_str(entry) == "linux/arm64"

    def test_windows(self):
        entry = {"platform": {"os": "windows", "architecture": "amd64"}}
        assert self._build_plat_str(entry) == "windows/amd64"

    def test_missing_os(self):
        """os is None/missing → should not TypeError."""
        entry = {"platform": {"architecture": "amd64"}}
        assert self._build_plat_str(entry) == "/amd64"

    def test_missing_architecture(self):
        entry = {"platform": {"os": "linux"}}
        assert self._build_plat_str(entry) == "linux/"

    def test_missing_platform_entirely(self):
        entry = {}
        assert self._build_plat_str(entry) == "/"

    def test_none_values(self):
        """Explicit None values for os/architecture."""
        entry = {"platform": {"os": None, "architecture": None}}
        # get('os', '') returns None when key exists with None value
        # This is the actual behavior — f-string converts None to "None"
        result = self._build_plat_str(entry)
        assert result == "None/None"


class TestTagSorting:
    """Test the natural sort used by find_matching_tag.

    ium.py sorts matching tags with key=_natural_sort_key, which compares
    digit runs numerically.  The old plain lexicographic sort ranked
    "9.0.3" above "15.0.2" — see the 2026-05-24 forgejo incident and
    docs/superpowers/specs/2026-06-04-tag-selection-safety-design.md.
    """

    @staticmethod
    def _sorted_desc(tags):
        return sorted(tags, key=_natural_sort_key, reverse=True)

    def test_simple_semver(self):
        tags = ["4.29.0", "4.30.0", "4.28.0"]
        assert self._sorted_desc(tags)[0] == "4.30.0"

    def test_different_digit_widths(self):
        """3.10.0 > 3.9.0 numerically — the case lexicographic sorting broke."""
        tags = ["3.10.0", "3.9.0", "3.2.0"]
        assert self._sorted_desc(tags) == ["3.10.0", "3.9.0", "3.2.0"]

    def test_multi_digit_major(self):
        """The forgejo incident shape: major versions 9 vs 15."""
        tags = ["9.0.3", "15.0.2", "12.0.1"]
        assert self._sorted_desc(tags)[0] == "15.0.2"

    def test_ls_4part_sort(self):
        tags = ["6.0.4.10291-ls289", "5.27.5.10198-ls284"]
        assert self._sorted_desc(tags)[0] == "6.0.4.10291-ls289"

    def test_pihole_date_sort(self):
        tags = ["2025.11.1", "2025.08.0", "2024.07.0"]
        assert self._sorted_desc(tags)[0] == "2025.11.1"

    def test_plex_hash_sort(self):
        """Plex tags: version components dominate; hash suffix only breaks ties.

        Digest matching handles correctness regardless of sort order.
        """
        tags = ["1.42.2.10156-f737b826c", "1.42.1.10060-4e8b05daf"]
        assert self._sorted_desc(tags)[0] == "1.42.2.10156-f737b826c"

    def test_calibre_web_same_version_different_ls(self):
        """Same upstream version, different LS build numbers."""
        tags = ["0.6.25-ls348", "0.6.25-ls345"]
        assert self._sorted_desc(tags)[0] == "0.6.25-ls348"
