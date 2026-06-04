# Tag Selection Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop ium from ever selecting a wrong "newest" tag during registry errors — fix the lexicographic sort, restrict the fallback to true 404s, and add a downgrade guard.

**Architecture:** Three independent defenses in `ium.py`: (1) a module-level `_natural_sort_key()` replacing lexicographic tag sorting, (2) a `DigestStatus` enum threaded through `_get_manifest_digest_head` so `find_matching_tag` can distinguish "tag missing" from "transient error", (3) a downgrade guard in `check_and_update` that reports but never auto-applies a candidate older than the current version.

**Tech Stack:** Python 3.8+ stdlib (`enum`, `re`), `requests`, pytest with `unittest.mock` (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-06-04-tag-selection-safety-design.md`

**Context for the implementer:**
- All registry HTTP goes through `DockerImageUpdater._request_with_retry` (`ium.py:292`), which retries 5xx/connection errors 3 times then *returns the failing response* (5xx) or *raises* (connection). A persistent 503 therefore surfaces as `requests.HTTPError` from `raise_for_status()`.
- Mocked-HTTP test style lives in `tests/test_auth_discovery.py` — patch `ium.requests.request` (and `ium.time.sleep` when the retry loop runs).
- Live tests (`tests/test_live.py`) hit real registries and are marked `live` (see `pytest.ini`). Run the non-live suite with `python -m pytest tests/ -m "not live"`.
- Run all commands from the repo root: `/Users/brendanl/src/ium`.

---

### Task 1: `_natural_sort_key` module-level function

**Files:**
- Modify: `ium.py` (new function after the constants block, ~line 58)
- Test: `tests/test_tag_selection.py` (new file)
- Modify: `tests/test_parsing.py:126-170` (`TestTagSorting` documents the old lexicographic behavior — rewrite)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tag_selection.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tag_selection.py -v`
Expected: FAIL at import — `ImportError: cannot import name '_natural_sort_key' from 'ium'`

- [ ] **Step 3: Implement `_natural_sort_key`**

In `ium.py`, immediately after the constants block (after `MANIFEST_ACCEPT_HEADER`, ~line 58), add:

```python
def _natural_sort_key(tag: str) -> tuple:
    """Sort key ordering digit runs numerically and text runs lexically.

    "v9.8.0-ls399" -> ((1,'v'), (0,9), (1,'.'), (0,8), (1,'.'), (0,0),
                       (1,'-ls'), (0,399))

    The (0, int) / (1, str) tagging keeps any two keys mutually comparable
    (Python 3 raises TypeError on bare int < str), with numbers ordering
    before text when shapes differ.  Plain lexicographic sorting ranked
    "9.0.3" above "15.0.2" and downgraded a production forgejo install.
    """
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r'(\d+)', tag)
        if part
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tag_selection.py -v`
Expected: 8 passed

- [ ] **Step 5: Rewrite `TestTagSorting` in `tests/test_parsing.py`**

Replace the entire `TestTagSorting` class (lines 126-170 — it documents the old
`sort(reverse=True)` lexicographic behavior, including a test that *asserts the
bug* as a known limitation). New class:

```python
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
```

Add to the imports at the top of `tests/test_parsing.py`:

```python
from ium import _natural_sort_key
```

(Check the existing import block first — if it already imports from `ium`, extend
that line instead of adding a duplicate import.)

Keep any remaining `TestTagSorting` tests that don't conflict (e.g. the Plex
hash-suffix test documenting arbitrary order) — adapt them to `_natural_sort_key`
if they still express something true, drop them if they only documented the
lexicographic limitation.

- [ ] **Step 6: Run both test files**

Run: `python -m pytest tests/test_tag_selection.py tests/test_parsing.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add ium.py tests/test_tag_selection.py tests/test_parsing.py
git commit -m "feat: natural version sort key for tag ordering"
```

---

### Task 2: `DigestStatus` enum and `_get_manifest_digest_head` status return

**Files:**
- Modify: `ium.py:600-639` (`_get_manifest_digest_head`) + imports + new enum
- Test: `tests/test_tag_selection.py` (append)
- Modify: `tests/test_live.py:141-194` (`TestDigestFetching` unpacks the old single return)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tag_selection.py` (and extend the `ium` import line with
`DigestStatus`):

```python
class TestDigestStatus:
    """_get_manifest_digest_head returns (digest, status) and classifies failures.

    404 -> NOT_FOUND (the tag does not exist; fallback is legitimate).
    Everything else -> ERROR (transient/auth/protocol; result unknown).
    """

    URL_ARGS = ("codeberg.org", "forgejo", "forgejo", "15", "tok")

    @patch("ium.requests.request")
    def test_success_returns_digest_and_ok(self, mock_request, updater):
        mock_request.return_value = _mock_response(
            200, headers={"Docker-Content-Digest": "sha256:abc"}
        )
        digest, status = updater._get_manifest_digest_head(*self.URL_ARGS)
        assert digest == "sha256:abc"
        assert status is DigestStatus.OK

    @patch("ium.requests.request")
    def test_404_is_not_found(self, mock_request, updater):
        mock_request.return_value = _mock_response(404)
        digest, status = updater._get_manifest_digest_head(*self.URL_ARGS)
        assert digest is None
        assert status is DigestStatus.NOT_FOUND

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_persistent_503_is_error(self, mock_request, _sleep, updater):
        # _request_with_retry tries 4 times (initial + 3 retries) then
        # returns the failing response; raise_for_status -> HTTPError(503).
        mock_request.side_effect = [_mock_response(503)] * 4
        digest, status = updater._get_manifest_digest_head(*self.URL_ARGS)
        assert digest is None
        assert status is DigestStatus.ERROR
        assert mock_request.call_count == 4

    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_connection_error_is_error(self, mock_request, _sleep, updater):
        mock_request.side_effect = requests.ConnectionError("boom")
        digest, status = updater._get_manifest_digest_head(*self.URL_ARGS)
        assert digest is None
        assert status is DigestStatus.ERROR

    @patch("ium.requests.request")
    def test_401_is_error_not_not_found(self, mock_request, updater):
        # Expired/invalid token must not look like a missing tag.
        mock_request.return_value = _mock_response(401)
        digest, status = updater._get_manifest_digest_head(*self.URL_ARGS)
        assert digest is None
        assert status is DigestStatus.ERROR

    @patch("ium.requests.request")
    def test_200_without_digest_header_is_error(self, mock_request, updater):
        # A 200 missing Docker-Content-Digest previously looked identical
        # to "tag not found".  It is a protocol anomaly: ERROR.
        mock_request.return_value = _mock_response(200, headers={})
        digest, status = updater._get_manifest_digest_head(*self.URL_ARGS)
        assert digest is None
        assert status is DigestStatus.ERROR
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tag_selection.py -v`
Expected: `TestDigestStatus` fails at import — `ImportError: cannot import name 'DigestStatus'`

- [ ] **Step 3: Implement the enum and new return shape**

In `ium.py`:

(a) Add to the import block (after `from contextlib import contextmanager`):

```python
from enum import Enum
```

(b) After the constants block, next to `_natural_sort_key`, add:

```python
class DigestStatus(Enum):
    """Outcome of a manifest digest HEAD request."""
    OK = "ok"
    NOT_FOUND = "not_found"   # registry said 404: the tag does not exist
    ERROR = "error"           # transient/auth/protocol failure: result unknown
```

(c) Replace the body of `_get_manifest_digest_head` (currently `ium.py:600-639`)
so the signature and behavior become:

```python
    def _get_manifest_digest_head(self, registry: str, namespace: str, repo: str,
                                   tag: str, token: Optional[str]
                                   ) -> Tuple[Optional[str], DigestStatus]:
        """
        Get manifest digest using HEAD request (faster, no body transfer).

        Returns the Docker-Content-Digest header which is the digest of the
        manifest list for multi-arch images, or the manifest itself for
        single-arch.  This is more correct for comparison than parsing
        manifest list JSON.

        Args:
            registry: Registry hostname
            namespace: Image namespace
            repo: Repository name
            tag: Tag name
            token: Authentication token

        Returns:
            (digest, DigestStatus.OK) on success.
            (None, DigestStatus.NOT_FOUND) when the registry returns 404.
            (None, DigestStatus.ERROR) on any other failure — 5xx, timeout,
            connection error, auth failure, or a 200 response missing the
            Docker-Content-Digest header.  Callers must not treat ERROR as
            "tag does not exist".
        """
        manifest_url = f"https://{registry}/v2/{namespace}/{repo}/manifests/{tag}"

        headers = {
            'Accept': MANIFEST_ACCEPT_HEADER
        }
        if token:
            headers['Authorization'] = f'Bearer {token}'

        try:
            response = self._request_with_retry('HEAD', manifest_url, headers=headers)
            response.raise_for_status()
            digest = response.headers.get('Docker-Content-Digest')
            if not digest:
                self.logger.debug(
                    f"No Docker-Content-Digest header for {namespace}/{repo}:{tag}"
                )
                return None, DigestStatus.ERROR
            return digest, DigestStatus.OK
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                self.logger.debug(f"Tag not found: {namespace}/{repo}:{tag}")
                return None, DigestStatus.NOT_FOUND
            self.logger.debug(
                f"HTTP error getting manifest digest for {namespace}/{repo}:{tag}: {e}"
            )
            return None, DigestStatus.ERROR
        except requests.RequestException as e:
            self.logger.debug(
                f"Error getting manifest digest for {namespace}/{repo}:{tag}: {e}"
            )
            return None, DigestStatus.ERROR
```

(d) `find_matching_tag` still uses the old single-value return at three call
sites (`ium.py:736`, `:768`, `:790`) — it now breaks. Task 3 rewrites it
properly; to keep the suite green *within this task*, make the minimal
mechanical unpack at each site:

- Line 736: `base_digest, _base_status = self._get_manifest_digest_head(...)`
- Line 768 (inside `fetch_digest`): `digest, _status = self._get_manifest_digest_head(...)`
- Line 790: `latest_digest, _latest_status = self._get_manifest_digest_head(...)`

Behavior is unchanged in this task (digest is `None` exactly when it was before).

- [ ] **Step 4: Update `tests/test_live.py` for the new return shape**

In `TestDigestFetching` (lines 141-194) and anywhere else in the file that calls
`_get_manifest_digest_head` directly, unpack the tuple. Pattern (apply to each
call site — lines 146, 154, 163, 176, 189):

```python
        digest, status = updater._get_manifest_digest_head(
            DEFAULT_REGISTRY, "linuxserver", "calibre", "latest", token
        )
        assert status is DigestStatus.OK
        assert digest is not None
        assert digest.startswith("sha256:")
```

Add `DigestStatus` to the `from ium import ...` line in `tests/test_live.py`.
These are live tests — they won't run in the default suite, but they must
at least still import cleanly (collection happens even for deselected tests).

- [ ] **Step 5: Run the non-live suite**

Run: `python -m pytest tests/ -m "not live" -v`
Expected: all pass (including the 6 new `TestDigestStatus` tests)

- [ ] **Step 6: Commit**

```bash
git add ium.py tests/test_tag_selection.py tests/test_live.py
git commit -m "feat: classify manifest HEAD failures as OK/NOT_FOUND/ERROR"
```

---

### Task 3: `find_matching_tag` decision table

**Files:**
- Modify: `ium.py:735-799` (base-digest fetch through fallback, inside `find_matching_tag`)
- Test: `tests/test_tag_selection.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tag_selection.py`:

```python
FORGEJO_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+$"


class TestFindMatchingTagDecisionTable:
    """One test per row of the spec's decision table.

    | Base tag resolution        | Behavior                       |
    |----------------------------|--------------------------------|
    | OK + digest match          | return (tag, digest)           |
    | OK + no match              | None (skip cycle)              |
    | NOT_FOUND (true 404)       | fallback: newest matching tag  |
    | ERROR                      | None (skip cycle)              |

    These tests patch the internal helpers, not HTTP: the HTTP-level
    classification is covered by TestDigestStatus, and the full HTTP path
    by TestMay24Regression.
    """

    TAGS = ["latest", "9.0.3", "12.0.1", "15.0.1", "15.0.2"]

    def _setup(self, updater, digest_map):
        """Wire updater so _get_manifest_digest_head serves from digest_map.

        digest_map: tag -> (digest, DigestStatus); unlisted tags -> NOT_FOUND.
        """
        from ium import DigestStatus as DS
        updater._get_docker_token = MagicMock(return_value="tok")
        updater._get_all_tags = MagicMock(return_value=list(self.TAGS))
        updater.compiled_patterns[FORGEJO_PATTERN] = re.compile(FORGEJO_PATTERN)

        def head(registry, namespace, repo, tag, token):
            return digest_map.get(tag, (None, DS.NOT_FOUND))

        updater._get_manifest_digest_head = MagicMock(side_effect=head)

    def test_base_ok_with_digest_match_returns_it(self, updater):
        self._setup(updater, {
            "15": ("sha256:current", DigestStatus.OK),
            "15.0.2": ("sha256:current", DigestStatus.OK),
            "15.0.1": ("sha256:older", DigestStatus.OK),
            "12.0.1": ("sha256:old", DigestStatus.OK),
            "9.0.3": ("sha256:ancient", DigestStatus.OK),
        })
        result = updater.find_matching_tag(
            "forgejo/forgejo", "15", FORGEJO_PATTERN, "codeberg.org"
        )
        assert result == ("15.0.2", "sha256:current")

    def test_base_ok_no_match_skips_cycle(self, updater):
        # Mid-release race: base repointed, version tag not pushed yet.
        self._setup(updater, {
            "15": ("sha256:brand-new", DigestStatus.OK),
            "15.0.2": ("sha256:current", DigestStatus.OK),
            "15.0.1": ("sha256:older", DigestStatus.OK),
            "12.0.1": ("sha256:old", DigestStatus.OK),
            "9.0.3": ("sha256:ancient", DigestStatus.OK),
        })
        result = updater.find_matching_tag(
            "forgejo/forgejo", "15", FORGEJO_PATTERN, "codeberg.org"
        )
        assert result is None  # no guessing; retried next cycle

    def test_base_ok_per_tag_errors_skip_cycle(self, updater):
        # Per-tag HEADs failed transiently: matches may have been missed.
        self._setup(updater, {
            "15": ("sha256:current", DigestStatus.OK),
            "15.0.2": (None, DigestStatus.ERROR),
            "15.0.1": (None, DigestStatus.ERROR),
            "12.0.1": (None, DigestStatus.ERROR),
            "9.0.3": (None, DigestStatus.ERROR),
        })
        result = updater.find_matching_tag(
            "forgejo/forgejo", "15", FORGEJO_PATTERN, "codeberg.org"
        )
        assert result is None

    def test_base_not_found_falls_back_to_natural_newest(self, updater):
        # True 404 on the base tag: fallback fires, and the natural sort
        # must pick 15.0.2 — the old lexicographic sort picked 9.0.3.
        self._setup(updater, {
            "15": (None, DigestStatus.NOT_FOUND),
            "15.0.2": ("sha256:current", DigestStatus.OK),
            "9.0.3": ("sha256:ancient", DigestStatus.OK),
        })
        result = updater.find_matching_tag(
            "forgejo/forgejo", "15", FORGEJO_PATTERN, "codeberg.org"
        )
        assert result == ("15.0.2", "sha256:current")

    def test_base_not_found_fallback_digest_error_skips_cycle(self, updater):
        self._setup(updater, {
            "15": (None, DigestStatus.NOT_FOUND),
            "15.0.2": (None, DigestStatus.ERROR),
        })
        result = updater.find_matching_tag(
            "forgejo/forgejo", "15", FORGEJO_PATTERN, "codeberg.org"
        )
        assert result is None

    def test_base_error_skips_cycle_before_tag_listing(self, updater):
        # The May 24 trigger: transient error on the base tag.  Must skip
        # without even listing tags — there is nothing safe to do.
        self._setup(updater, {
            "15": (None, DigestStatus.ERROR),
        })
        result = updater.find_matching_tag(
            "forgejo/forgejo", "15", FORGEJO_PATTERN, "codeberg.org"
        )
        assert result is None
        updater._get_all_tags.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tag_selection.py::TestFindMatchingTagDecisionTable -v`
Expected: FAIL —
- `test_base_ok_no_match_skips_cycle` returns `("15.0.2", ...)` instead of `None` (old fallback fires)
- `test_base_not_found_falls_back_to_natural_newest` returns `("9.0.3", ...)` (lexicographic max)
- `test_base_error_skips_cycle_before_tag_listing` returns a fallback result instead of `None`
- others may fail similarly

- [ ] **Step 3: Implement the decision table**

In `find_matching_tag`, replace everything from the base-digest fetch
(currently `base_digest, _base_status = ...`, ~line 736) through the end of the
method (~line 799) with:

```python
        # Get digest for base tag using HEAD request
        base_digest, base_status = self._get_manifest_digest_head(
            registry, namespace, repo, base_tag, token
        )
        if base_status is DigestStatus.ERROR:
            # Transient/auth/protocol failure: we cannot know what the base
            # tag points at, so guessing is unsafe (2026-05-24 forgejo
            # incident).  Skip; the next cycle retries.
            self.logger.warning(
                f"Transient registry error resolving {image}:{base_tag}; "
                f"skipping check this cycle"
            )
            return None
        if base_status is DigestStatus.NOT_FOUND:
            self.logger.warning(f"Tag '{base_tag}' not found in registry for {image}")

        # Get all available tags
        all_tags = self._get_all_tags(registry, namespace, repo, token)
        if not all_tags:
            self.logger.error(f"Could not get tags for {image}")
            return None

        # Get cached compiled pattern
        pattern = self.compiled_patterns.get(regex_pattern)
        if not pattern:
            self.logger.error(f"Pattern not found in cache: '{regex_pattern}'")
            return None

        # Find tags matching the pattern
        matching_tags = [tag for tag in all_tags if pattern.match(tag)]
        self.logger.debug(f"Found {len(matching_tags)} tags matching pattern")

        if not matching_tags:
            self.logger.warning(f"No tags matching pattern '{regex_pattern}'")
            return None

        # Newest first.  Natural sort: digit runs compare numerically, so
        # 15.0.2 ranks above 9.0.3 (lexicographic sorting got this wrong).
        matching_tags.sort(key=_natural_sort_key, reverse=True)

        if base_status is DigestStatus.OK:
            # Find the version tag sharing the base tag's digest.
            def fetch_digest(tag: str) -> Tuple[str, Optional[str], DigestStatus]:
                digest, status = self._get_manifest_digest_head(
                    registry, namespace, repo, tag, token
                )
                return (tag, digest, status)

            # Use ThreadPoolExecutor for parallel fetching (limit concurrency
            # to be nice to registries)
            max_workers = min(10, len(matching_tags))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(fetch_digest, tag): tag for tag in matching_tags}

                for future in as_completed(futures):
                    tag, digest, status = future.result()
                    if status is DigestStatus.OK and digest == base_digest:
                        # Found a match - cancel remaining futures and return
                        for f in futures:
                            f.cancel()
                        self.logger.debug(f"Found matching tag {tag} with digest {digest[:16]}...")
                        return (tag, base_digest)

            # The base tag resolved but nothing matched: either a mid-release
            # race (registry repointed the base tag before pushing the new
            # version tag) or transient per-tag failures hid the match.
            # Both heal by themselves — never guess here.
            self.logger.warning(
                f"No tag matching pattern '{regex_pattern}' shares a digest "
                f"with {image}:{base_tag} (mid-release race or transient "
                f"registry errors); skipping check this cycle"
            )
            return None

        # Base tag genuinely does not exist (true 404, e.g. a repo that
        # publishes only version tags): fall back to the newest matching tag.
        latest_tag = matching_tags[0]
        latest_digest, latest_status = self._get_manifest_digest_head(
            registry, namespace, repo, latest_tag, token
        )
        if latest_status is DigestStatus.OK:
            self.logger.info(
                f"Using latest matching tag '{latest_tag}' for {image}"
                f" (base tag '{base_tag}' does not exist in the registry)"
            )
            return (latest_tag, latest_digest)

        self.logger.error(f"Could not get digest for latest matching tag {image}:{latest_tag}")
        return None
```

Also update the method docstring's Returns section to mention:
`None` when the result is uncertain (transient errors, no digest match) —
the caller treats this as "skip this cycle".

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tag_selection.py -v`
Expected: all pass

- [ ] **Step 5: Run the full non-live suite**

Run: `python -m pytest tests/ -m "not live"`
Expected: all pass. If anything in `test_registry_override.py` or
`test_multi_container_update.py` fails, inspect before changing it — those
patch `find_matching_tag` wholesale and should be unaffected; a failure there
means this task changed something it shouldn't have.

- [ ] **Step 6: Commit**

```bash
git add ium.py tests/test_tag_selection.py
git commit -m "fix: never guess a tag on transient registry errors

Fallback to newest matching tag now fires only on a true 404 of the
base tag, and 'newest' uses natural version ordering. Transient
errors and digest mismatches skip the cycle and retry next run."
```

---

### Task 4: Downgrade guard in `check_and_update`

**Files:**
- Modify: `ium.py:1409-1437` (the `old_tag != matching_tag` branch in `check_and_update`)
- Test: `tests/test_tag_selection.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tag_selection.py`:

```python
def _make_updater(tmp_path, state):
    """Updater with the forgejo config and the given persisted state dict."""
    config = {
        "images": [{
            "image": "forgejo/forgejo",
            "regex": FORGEJO_PATTERN,
            "base_tag": "15",
            "registry": "codeberg.org",
            "auto_update": True,
            "cleanup_old_images": False,
        }]
    }
    config_file = tmp_path / "config.json"
    state_file = tmp_path / "state.json"
    config_file.write_text(json.dumps(config))
    state_file.write_text(json.dumps(state))
    return DockerImageUpdater(str(config_file), str(state_file))


CURRENT_STATE = {
    "forgejo/forgejo": {
        "base_tag": "15",
        "tag": "15.0.2",
        "digest": "sha256:current",
        "last_updated": "2026-05-24T00:00:00",
    }
}


class TestDowngradeGuard:
    """A candidate older than the current version is reported, never applied."""

    @patch("ium.send_notifications")
    def test_downgrade_reported_but_not_applied(self, mock_notify, tmp_path):
        u = _make_updater(tmp_path, CURRENT_STATE)
        u.find_matching_tag = MagicMock(return_value=("9.0.3", "sha256:ancient"))
        u._get_containers_for_image = MagicMock(return_value=[])
        u._pull_image = MagicMock(return_value=True)

        updates = u.check_and_update()

        assert len(updates) == 1
        info = updates[0]
        assert info["new_tag"] == "9.0.3"
        assert info["downgrade"] is True
        # Effective auto_update is off: webui history derives
        # applied=... from this field.
        assert info["auto_update"] is False
        u._pull_image.assert_not_called()
        # State still moves (existing convention: prevents hourly
        # re-notification; a bogus candidate self-heals next cycle).
        assert u.state["forgejo/forgejo"].tag == "9.0.3"
        # Notification still goes out, marked not-auto-applied.
        assert mock_notify.call_args.kwargs["auto_update"] is False

    @patch("ium.send_notifications")
    def test_upgrade_applies_normally(self, mock_notify, tmp_path):
        u = _make_updater(tmp_path, CURRENT_STATE)
        u.find_matching_tag = MagicMock(return_value=("15.0.3", "sha256:new"))
        u._get_containers_for_image = MagicMock(return_value=[])
        u._pull_image = MagicMock(return_value=True)

        updates = u.check_and_update()

        assert len(updates) == 1
        info = updates[0]
        assert info["downgrade"] is False
        assert info["auto_update"] is True
        u._pull_image.assert_called()
        assert u.state["forgejo/forgejo"].tag == "15.0.3"

    @patch("ium.send_notifications")
    def test_unknown_old_tag_is_not_blocked(self, mock_notify, tmp_path):
        # No state and no containers -> old_tag == 'unknown': nothing to
        # compare against, guard must not fire.
        u = _make_updater(tmp_path, {})
        u.find_matching_tag = MagicMock(return_value=("9.0.3", "sha256:ancient"))
        u._get_containers_for_image = MagicMock(return_value=[])
        u._pull_image = MagicMock(return_value=True)

        updates = u.check_and_update()

        assert len(updates) == 1
        assert updates[0]["downgrade"] is False
        assert updates[0]["auto_update"] is True
        u._pull_image.assert_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tag_selection.py::TestDowngradeGuard -v`
Expected: FAIL — `KeyError: 'downgrade'` (field doesn't exist yet) and
`_pull_image` *is* called in the downgrade test.

- [ ] **Step 3: Implement the guard**

In `check_and_update`, the branch currently reading (`ium.py:1409-1420`):

```python
                # Only report update if tags are actually different
                if old_tag != matching_tag:
                    self.logger.info(f"UPDATE AVAILABLE: {old_tag} -> {matching_tag}")

                    update_info = {
                        'image': image,
                        'base_tag': base_tag,
                        'old_tag': old_tag,
                        'new_tag': matching_tag,
                        'digest': digest,
                        'auto_update': auto_update
                    }
```

becomes:

```python
                # Only report update if tags are actually different
                if old_tag != matching_tag:
                    # Downgrade guard: a candidate older than the current
                    # version is reported but never auto-applied.  Last line
                    # of defense against bad candidates (2026-05-24 forgejo
                    # incident); a genuine upstream rollback can still be
                    # applied manually.
                    is_downgrade = (
                        old_tag not in (None, 'unknown')
                        and _natural_sort_key(matching_tag) < _natural_sort_key(old_tag)
                    )
                    if is_downgrade:
                        self.logger.warning(
                            f"DOWNGRADE DETECTED: {image} candidate {matching_tag} "
                            f"is older than current {old_tag}; reporting but not "
                            f"auto-applying"
                        )
                    effective_auto_update = auto_update and not is_downgrade

                    self.logger.info(f"UPDATE AVAILABLE: {old_tag} -> {matching_tag}")

                    update_info = {
                        'image': image,
                        'base_tag': base_tag,
                        'old_tag': old_tag,
                        'new_tag': matching_tag,
                        'digest': digest,
                        'auto_update': effective_auto_update,
                        'downgrade': is_downgrade
                    }
```

Then two more changes in the same branch:

(a) The notification call (currently `ium.py:1427-1431`) passes the effective flag:

```python
                    send_notifications(
                        self.config.get('notifications'),
                        image=image, old_version=old_tag, new_version=matching_tag,
                        event='update_found', digest=digest,
                        auto_update=effective_auto_update
                    )
```

(b) The apply gate (currently `ium.py:1434`) changes from `if auto_update:` to:

```python
                    update_ok = True
                    if effective_auto_update:
```

Leave the state-update condition (`if not auto_update or update_ok:`,
currently `ium.py:1465`) untouched: for a blocked downgrade `update_ok`
stays `True`, so state still moves — intentional, per the spec.

Do NOT touch the `IMAGE REBUILT` branch (`ium.py:1472+`): same tag on both
sides, a downgrade is impossible there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tag_selection.py -v`
Expected: all pass

- [ ] **Step 5: Run the full non-live suite**

Run: `python -m pytest tests/ -m "not live"`
Expected: all pass. Watch `test_multi_container_update.py` and
`test_registry_override.py` (they drive `check_and_update` with mocked
`find_matching_tag`): their fixtures update *to a newer* tag, so the guard
must not fire. If one fails with `_pull_image`/`_update_containers` not
called, the guard is misfiring — check the `is_downgrade` comparison against
that test's old/new tags rather than loosening the test.

- [ ] **Step 6: Commit**

```bash
git add ium.py tests/test_tag_selection.py
git commit -m "feat: report but never auto-apply downgrade candidates"
```

---

### Task 5: May 24 regression replay (HTTP level)

**Files:**
- Test: `tests/test_tag_selection.py` (append)

- [ ] **Step 1: Write the regression test**

This should pass immediately — it replays the incident end-to-end through the
real `find_matching_tag` HTTP path and proves the cycle is skipped. It exists
so the incident can never silently come back.

Append to `tests/test_tag_selection.py`:

```python
class TestMay24Regression:
    """Replay of the 2026-05-24 incident, HTTP level.

    forgejo/forgejo tracked with base_tag '15' on codeberg.org; the tag
    list contains 9.0.3 and 15.0.x; the base-tag HEAD hits a persistent
    503.  Before the fix: fallback + lexicographic sort selected 9.0.3
    and 'applied' it.  After: the cycle is skipped, state untouched.
    """

    @patch("ium.send_notifications")
    @patch("ium.time.sleep")
    @patch("ium.requests.request")
    def test_transient_base_error_skips_cycle(
        self, mock_request, _sleep, mock_notify, tmp_path
    ):
        u = _make_updater(tmp_path, CURRENT_STATE)
        u._get_docker_token = MagicMock(return_value="tok")
        u._get_containers_for_image = MagicMock(return_value=[])
        u._pull_image = MagicMock(return_value=True)

        # HEAD /v2/forgejo/forgejo/manifests/15 -> 503, all 4 attempts.
        mock_request.side_effect = [_mock_response(503)] * 4

        updates = u.check_and_update()

        assert updates == []
        mock_notify.assert_not_called()
        u._pull_image.assert_not_called()
        # State untouched: still on 15.0.2.
        assert u.state["forgejo/forgejo"].tag == "15.0.2"
        assert u.state["forgejo/forgejo"].digest == "sha256:current"
        # Only the base-tag HEAD was attempted - no tag listing happened.
        assert all("/manifests/15" in c.args[1] for c in mock_request.call_args_list)
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_tag_selection.py::TestMay24Regression -v`
Expected: PASS. If it fails, a previous task is incomplete — fix the
implementation there; do not adjust this test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_tag_selection.py
git commit -m "test: regression replay of 2026-05-24 forgejo downgrade incident"
```

---

### Task 6: Version bump and final verification

**Files:**
- Modify: `ium.py:10` (`__version__`)

- [ ] **Step 1: Bump the version**

In `ium.py` line 10:

```python
__version__ = "1.2.4"
```

- [ ] **Step 2: Run the complete non-live suite**

Run: `python -m pytest tests/ -m "not live" -v`
Expected: all pass, zero failures, zero errors. The suite had 371 non-live
tests before this work; expect ~26 more.

- [ ] **Step 3: Optionally run live tests (network required)**

Run: `python -m pytest tests/test_live.py -m live -v`
Expected: pass (requires network; codeberg/Docker Hub/ghcr reachable). Skip
this step if offline — but note it in the final report.

- [ ] **Step 4: Commit**

```bash
git add ium.py
git commit -m "chore: bump version to 1.2.4"
```

Do NOT tag `v1.2.4` — the user decides when to release.
