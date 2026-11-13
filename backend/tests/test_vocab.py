import asyncio
import json

import httpx
import pytest

from wda.cache import TTLCache
from wda.edusharing.client import EduSharingClient
from wda.edusharing.errors import EduSharingError
from wda.edusharing.vocab import UnresolvedValue, VocabStore
from wda.instances import Instance

from .esfake import DISC, EC, MDS_PATH, FakeRepo

INSTANCE = Instance(id="t", name="T", base_url="https://repo.example")


class Clock:
    now = 1_000_000.0

    def __call__(self):
        return self.now


def make(repo, tmp_path, clock=None, ttl=86400):
    http = httpx.AsyncClient(transport=repo.transport())

    async def sleep(_):
        return None

    client = EduSharingClient(INSTANCE, http, TTLCache(ttl=900), sleep=sleep)
    return VocabStore(client, tmp_path, ttl=ttl, clock=clock or Clock())


def run(coro):
    return asyncio.run(coro)


def mds_calls(repo):
    return [r for r in repo.requests if r.url.path == MDS_PATH]


def test_fields_are_trimmed_and_deduplicated(tmp_path):
    repo = FakeRepo()
    fields = run(make(repo, tmp_path).fields())
    assert list(fields) == ["ccm:taxonid", "ccm:educationalcontext", "ccm:commonlicense_key",
                            "cclom:title"]
    taxon = fields["ccm:taxonid"]
    assert (taxon.caption, taxon.type, len(taxon.values)) == ("Fach", "multivalueTree", 7)
    assert taxon.values[1].id == DISC + "380"
    assert taxon.values[1].parent == DISC
    assert fields["ccm:commonlicense_key"].caption == "Lizenz"
    assert fields["ccm:commonlicense_key"].values == ()


def test_disk_copy_is_trimmed(tmp_path):
    repo = FakeRepo()
    run(make(repo, tmp_path).fields())
    path = tmp_path / "cache" / "mds-t.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    assert "views" not in stored and "<b>groß</b>" not in text
    assert '"description"' not in text and '"icon"' not in text
    assert stored["instance"] == "t" and stored["metadataset"] == "mds_oeh"


def test_resolve(tmp_path):
    store = make(FakeRepo(), tmp_path)

    async def scenario():
        r = await store.resolve("ccm:taxonid", DISC + "380")
        assert (r.value, r.label, r.how) == (DISC + "380", "Mathematik", "uri")
        r = await store.resolve("ccm:taxonid", "  mathematik ")
        assert (r.value, r.label, r.how) == (DISC + "380", "Mathematik", "label")
        r = await store.resolve("ccm:educationalcontext", "Sekundarstufe I")
        assert r.value == EC + "sekundarstufe_1"
        r = await store.resolve("ccm:commonlicense_key", "CC_BY")
        assert (r.value, r.label, r.how) == ("CC_BY", "CC_BY", "literal")
        r = await store.resolve("license", "OPEN")
        assert r.how == "literal"
        r = await store.resolve("ccm:commonlicense_key", "")
        assert (r.value, r.how) == ("", "literal")

    run(scenario())


def test_ambiguous_label(tmp_path):
    store = make(FakeRepo(), tmp_path)
    with pytest.raises(UnresolvedValue) as info:
        run(store.resolve("ccm:taxonid", "Sport"))
    err = info.value
    assert err.property == "ccm:taxonid" and err.text == "Sport"
    assert {s["id"] for s in err.suggestions} == {DISC + "900", DISC + "901"}
    assert "mehrdeutig" in str(err)


def test_unknown_label_gives_suggestions(tmp_path):
    store = make(FakeRepo(), tmp_path)
    with pytest.raises(UnresolvedValue) as info:
        run(store.resolve("ccm:taxonid", "Griech"))
    captions = [s["caption"] for s in info.value.suggestions]
    assert captions[:2] == ["Griechisch", "Alt-Griechisch"]
    with pytest.raises(UnresolvedValue) as info:
        run(store.resolve("ccm:taxonid", "Mathematk"))
    assert info.value.suggestions[0]["caption"] == "Mathematik"
    with pytest.raises(UnresolvedValue) as info:
        run(store.resolve("ccm:taxonid", "zzzzqqq"))
    assert info.value.suggestions == []
    with pytest.raises(UnresolvedValue):
        run(store.resolve("ccm:taxonid", ""))


def test_label_lookup(tmp_path):
    store = make(FakeRepo(), tmp_path)

    async def scenario():
        assert await store.label("ccm:taxonid", DISC + "460") == "Physik"
        assert await store.label("ccm:educationalcontext", "political") == "political"
        assert await store.label("ccm:educationalcontext", "") == ""
        assert await store.label("gibt:es:nicht", "x") == "x"
        assert await store.field("gibt:es:nicht") is None

    run(scenario())


def test_loaded_once_and_reused_from_disk(tmp_path):
    repo = FakeRepo()
    clock = Clock()
    store = make(repo, tmp_path, clock)

    async def twice():
        await asyncio.gather(store.fields(), store.fields(), store.fields())
        await store.fields()

    run(twice())
    assert len(mds_calls(repo)) == 1
    repo2 = FakeRepo()
    fields = run(make(repo2, tmp_path, clock).fields())
    assert mds_calls(repo2) == [] and "ccm:taxonid" in fields


def test_expired_copy_is_refreshed(tmp_path):
    repo = FakeRepo()
    clock = Clock()
    run(make(repo, tmp_path, clock, ttl=100).fields())
    clock.now += 101
    repo2 = FakeRepo()
    run(make(repo2, tmp_path, clock, ttl=100).fields())
    assert len(mds_calls(repo2)) == 1


def test_expired_copy_survives_network_failure(tmp_path):
    clock = Clock()
    run(make(FakeRepo(), tmp_path, clock, ttl=100).fields())
    clock.now += 101
    broken = FakeRepo()
    broken.mds_error = httpx.ConnectError("weg")
    store = make(broken, tmp_path, clock, ttl=100)
    fields = run(store.fields())
    assert "ccm:taxonid" in fields
    assert store.stale is True


def test_no_copy_and_network_failure(tmp_path):
    broken = FakeRepo()
    broken.mds_error = httpx.Response(500, text="kaputt")
    with pytest.raises(EduSharingError) as info:
        run(make(broken, tmp_path).fields())
    assert info.value.kind == "unavailable"


def test_corrupt_or_foreign_copy_is_ignored(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "mds-t.json").write_text("{kaputt", encoding="utf-8")
    repo = FakeRepo()
    assert "ccm:taxonid" in run(make(repo, tmp_path).fields())
    assert len(mds_calls(repo)) == 1
    # Kopie eines anderen Metadatensets derselben Instanz-ID zählt nicht
    path = tmp_path / "cache" / "mds-t.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["metadataset"] = "anderes"
    path.write_text(json.dumps(stored), encoding="utf-8")
    repo2 = FakeRepo()
    run(make(repo2, tmp_path).fields())
    assert len(mds_calls(repo2)) == 1


def test_unwritable_data_dir_still_works(tmp_path):
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    repo = FakeRepo()
    assert "ccm:taxonid" in run(make(repo, blocker).fields())


def test_malformed_metadataset(tmp_path):
    repo = FakeRepo()
    repo.mds_error = httpx.Response(200, json={"widgets": "keine liste"})
    with pytest.raises(EduSharingError) as info:
        run(make(repo, tmp_path).fields())
    assert info.value.kind == "bad_response"
