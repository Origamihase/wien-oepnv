"""Pin the post-fix peer-merge identity preservation behaviour.

Pre-fix ``deduplicate_fuzzy``'s peer-merge branch rewrote the merged
item's ``guid`` to ``sha256(new_title)`` and aliased
``_identity = guid``. The intent (per the now-deleted comment) was to
"ensure clients see it as a new/updated item", but it conflated two
keys with different responsibilities:

* ``_identity`` is the *server-side* dedup / ``first_seen`` key. It
  must stay STABLE across cycles or every cycle in which the merge
  grouping or the combined title shifts triggers a fresh ``first_seen``,
  and the merged disruption is perpetually republished as brand-new
  (FIFO retirement on age can never fire either).
* ``guid`` is the *client-side* identifier RSS readers use to track
  "is this the same item I've already seen?". Stable guid → stable
  user-side history. Updates flow through ``pubDate`` / ``title`` /
  ``description``, the channels clients actually use to detect updates.

Post-fix both fields are PRESERVED from the survivor item across a
peer-merge. Survivor selection is now deterministic via the
top-of-function sort by ``(_identity, guid, title)``, so the survivor
is the same item cycle to cycle and the merged item's identity stays
byte-identical.
"""

from src.feed.merge import deduplicate_fuzzy


def test_peer_merge_preserves_survivor_identity_and_guid() -> None:
    """Survivor's ``_identity`` AND ``guid`` carry forward unchanged."""
    items = [
        {
            "title": "U1: Störung Event",
            "description": "desc1",
            "guid": "guid1",
            "_calculated_identity": "calc_ident1",
            "_identity": "ident1",
        },
        {
            "title": "U1: Störung Event",
            "description": "desc2",
            "guid": "guid2",
            "_calculated_identity": "calc_ident2",
            "_identity": "ident2",
        },
    ]

    merged = deduplicate_fuzzy(items)
    assert len(merged) == 1
    merged_item = merged[0]

    # Survivor selection is deterministic via the top-of-function sort by
    # ``(_identity, guid, title)``. Both items share the same title and
    # ``_identity`` orders first → item 1 (``ident1`` / ``guid1``) wins.
    assert merged_item["_identity"] == "ident1", (
        "Survivor's pre-merge _identity must be preserved (not replaced by "
        "a sha256(new_title) rehash) so first_seen survives the merge."
    )
    assert merged_item["guid"] == "guid1", (
        "Survivor's pre-merge guid must be preserved so RSS clients keep "
        "the item as the same entry across cycles."
    )

    # Defensive cleanup of the stale ``_calculated_identity`` cache slot
    # is unchanged from the pre-fix code.
    assert "_calculated_identity" not in merged_item


def test_peer_merge_does_not_introduce_sha256_rehash() -> None:
    """The merged guid must not be ``sha256(new_title)`` — that was the bug."""
    import hashlib

    items = [
        {
            "title": "U1/U2: foo bar quux",
            "guid": "alpha",
            "_identity": "id_alpha",
        },
        {
            "title": "U1: foo bar baz",
            "guid": "beta",
            "_identity": "id_beta",
        },
    ]

    merged = deduplicate_fuzzy(items)
    assert len(merged) == 1
    merged_item = merged[0]

    new_title = merged_item["title"]
    pre_fix_rehash = hashlib.sha256(new_title.encode("utf-8")).hexdigest()
    assert merged_item["guid"] != pre_fix_rehash, (
        "Pre-fix peer-merge rehashed guid to sha256(new_title), resetting "
        "first_seen every cycle the combined title shifted. Post-fix the "
        "survivor's guid is preserved."
    )
    assert merged_item["guid"] in ("alpha", "beta")
