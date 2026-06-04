# Tag Selection Safety — Design

**Date:** 2026-06-04
**Status:** Approved
**Target version:** 1.2.4

## Problem

On 2026-05-24 a production ium instance downgraded a running forgejo container
from `15.0.2` to `9.0.3`, then "upgraded" it back one hour later. Config:

```json
{
  "image": "forgejo/forgejo",
  "regex": "^[0-9]+\\.[0-9]+\\.[0-9]+$",
  "base_tag": "15",
  "registry": "codeberg.org",
  "auto_update": true
}
```

Root cause chain:

1. A transient codeberg error (surviving the 3 retries in
   `_request_with_retry`) made the base-tag digest HEAD fail, so
   `_get_manifest_digest_head` returned `None`.
2. `find_matching_tag` cannot distinguish "tag does not exist" (404) from
   transient failure — both return `None` — so it took the fallback path:
   "use the latest matching tag".
3. "Latest" was computed with a plain lexicographic `sort(reverse=True)`,
   and `"9.0.3" > "15.0.2"` as strings. The fallback selected `9.0.3`.
4. `auto_update: true` applied it. (No container existed for the image on
   the affected host, so only the image/tags were updated — but with a
   running container this would have recreated it on a v9 image.)
5. Nothing compared the candidate against the known current version
   (`15.0.2` in state), so the downgrade went unchallenged.

Three independent defects, each fixed below.

## Decisions (settled during brainstorming)

| Question | Decision |
|---|---|
| When does the "newest matching tag" fallback apply? | Only on a **true 404** of the base tag. Any other failure skips the cycle. |
| Base digest OK but no matching tag shares it? | **Skip the cycle**, retry next run (covers mid-release races and transient per-tag HEAD failures). |
| Downgrade guard? | **Report, never auto-apply.** Notification/history still emitted (`applied: false`); manual apply remains possible. |
| Sort implementation | Generic natural sort, no dependencies (axis 1 option A). |
| Error plumbing | Status mini-enum returned alongside digest (axis 2 option A). |

## Design

### 1. Natural sort key

New module-level function in `ium.py`:

```python
def _natural_sort_key(tag: str) -> tuple:
    # "v9.8.0-ls399" -> ((1,'v'), (0,9), (1,'.'), (0,8), (1,'.'), (0,0), (1,'-ls'), (0,399))
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r'(\d+)', tag) if part
    )
```

- Digit runs compare numerically, text runs lexically.
- The `(0, int)` / `(1, str)` tagging keeps any two keys mutually comparable
  (Python 3 raises `TypeError` on bare `int < str`).
- Must handle every tag shape ium supports today: `15.0.2`,
  `6.1.1.10360-ls301`, `v9.8.0-ls399`, and arbitrary user regexes.

Used at the `matching_tags` sort (currently `ium.py:762`) and in the
downgrade guard.

### 2. Digest status enum

```python
class DigestStatus(Enum):
    OK = "ok"
    NOT_FOUND = "not_found"   # HTTP 404 only
    ERROR = "error"           # 5xx / timeout / connection / 401 / 403,
                              # or HTTP 200 missing Docker-Content-Digest
```

`_get_manifest_digest_head` signature changes from `-> Optional[str]` to
`-> Tuple[Optional[str], DigestStatus]`. Digest is non-None iff status is
`OK`. All three call sites (`find_matching_tag` base fetch, parallel per-tag
fetch, fallback fetch) are internal; no public API change.

A 200 response without the `Docker-Content-Digest` header is `ERROR`, not
`NOT_FOUND` — today it is silently indistinguishable from a missing tag.

### 3. `find_matching_tag` decision table

| Base tag resolution | Behavior |
|---|---|
| `OK`, a matching tag shares the digest | Return `(tag, digest)` — unchanged happy path. |
| `OK`, no digest match (mid-release race, or per-tag HEAD errors) | Warn, return `None` → cycle skipped, retried next run. |
| `NOT_FOUND` (true 404) | Fallback: newest matching tag by natural sort; if its digest fetch is not `OK`, return `None`. |
| `ERROR` | Warn loudly ("transient registry error … skipping check this cycle"), return `None`. |

The caller (`check_for_updates`, `ium.py:1381`) already treats `None` as
"warn and continue" with no state mutation, so skip paths need no caller
changes.

### 4. Downgrade guard

In the check loop (near `ium.py:1410`), after `old_tag` and `matching_tag`
are known:

- Guard applies when `old_tag` is known (not `None`/`'unknown'`) and
  `_natural_sort_key(matching_tag) < _natural_sort_key(old_tag)`.
- On downgrade candidate: emit `update_found` progress event, send
  notification, record history with `applied: false` — but **force
  auto-apply off** with a warning log, regardless of `auto_update`.
- State still updates to the candidate (existing convention: state moves on
  any detection to prevent re-reporting). Consequences:
  - Genuine upstream rollback + `auto_update: true`: one notification, no
    hourly spam; user applies manually if desired.
  - Bogus candidate (future unknown bug): next cycle sees the real version
    as an "update" and self-heals; `_update_containers` skips
    already-current containers, so no container churn.
- Equal keys are not a downgrade. Unparseable comparisons cannot occur
  (the key function totally orders all strings).

### 5. Error handling & logging summary

| Event | Log level | Cycle outcome |
|---|---|---|
| Base tag transient error | WARNING, explicit "skipping this cycle" | Skipped, state untouched |
| Base tag 404 | INFO (existing fallback message) | Fallback path |
| No digest match with healthy base | WARNING | Skipped, state untouched |
| Downgrade candidate | WARNING ("DOWNGRADE DETECTED … not auto-applying") | Reported, not applied, state updated |

### 6. Testing

New/updated tests (mocked HTTP, consistent with `tests/test_auth_discovery.py`
style):

1. **Natural sort:** `9.0.3` < `15.0.2`; `ls99` < `ls301` suffixes;
   `v`-prefix tags; 4-part `6.1.1.10360-ls301`; equal tags.
2. **`_get_manifest_digest_head` statuses:** 404 → `NOT_FOUND`;
   500/timeout/401 → `ERROR`; 200 without digest header → `ERROR`;
   success → `OK` + digest.
3. **`find_matching_tag`:** one test per decision-table row.
4. **Downgrade guard:** older candidate → reported, not applied, state
   updated; newer/equal → normal flow; `old_tag == 'unknown'` → not blocked.
5. **May 24 regression replay:** tag list containing `9.0.3` and `15.0.2`,
   base `15` HEAD returns 503 → cycle skipped, no candidate, no state change.

### 7. Out of scope

`_get_all_tags` (`ium.py:654`) does not follow the `Link` pagination header
of `/v2/<name>/tags/list`; a paginating registry could silently truncate the
tag list. Adjacent but independent bug — file a follow-up issue.

## Compatibility

- No config schema changes; no new dependencies.
- Behavior change: cycles that previously produced a (possibly wrong)
  fallback candidate during transient errors now skip and retry. Detection
  latency worst case: one check interval.
- Version bump: `1.2.4` (patch — bugfix only).
