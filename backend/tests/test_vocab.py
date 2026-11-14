import asyncio
import copy
import json

import httpx
import pytest

from wda.cache import TTLCache
from wda.edusharing.client import EduSharingClient
from wda.edusharing.errors import EduSharingError
from wda.edusharing.vocab import UnresolvedValue, VocabStore
from wda.instances import Instance

from .esfake import DISC, EC, MDS, MDS_PATH, FakeRepo

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
        assert (r.value, r.label, r.how) == ("", "", "literal")

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
        assert await store.label("ccm:educationalcontext", "") == "(leer)"
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


def test_empty_metadataset_keeps_good_copy(tmp_path):
    clock = Clock()
    run(make(FakeRepo(), tmp_path, clock, ttl=100).fields())
    clock.now += 101
    repo = FakeRepo()
    repo.mds_error = httpx.Response(200, json={"widgets": []})
    store = make(repo, tmp_path, clock, ttl=100)

    async def scenario():
        assert "ccm:taxonid" in await store.fields()
        resolved = await store.resolve("ccm:taxonid", "Mathematik")
        assert resolved.how == "label"

    run(scenario())
    assert store.stale is True
    stored = json.loads((tmp_path / "cache" / "mds-t.json").read_text(encoding="utf-8"))
    assert len(stored["fields"]) == 4


def test_failure_is_remembered_briefly(tmp_path):
    repo = FakeRepo()
    repo.mds_error = httpx.Response(503, text="x")
    clock = Clock()
    store = make(repo, tmp_path, clock)

    async def scenario():
        results = await asyncio.gather(*[store.fields() for _ in range(10)],
                                       return_exceptions=True)
        assert all(isinstance(r, EduSharingError) for r in results)
        assert len(mds_calls(repo)) == 3  # ein Ladeversuch mit zwei Wiederholungen
        clock.now += 31
        repo.mds_error = None
        assert "ccm:taxonid" in await store.fields()

    run(scenario())


def test_literal_and_facet_rules(tmp_path):
    store = make(FakeRepo(), tmp_path)

    async def scenario():
        r = await store.resolve("ccm:educationalcontext", "political", literal=True)
        assert (r.value, r.label, r.how) == ("political", "political", "literal")
        r = await store.resolve("ccm:taxonid", DISC + "380", literal=True)
        assert (r.value, r.label, r.how) == (DISC + "380", "Mathematik", "literal")
        r = await store.resolve("ccm:commonlicense_key", "  CC_BY ")
        assert r.value == "CC_BY"
        await store.check_facet("ccm:taxonid")            # hat eine Werteliste
        await store.check_facet("ccm:commonlicense_key")  # für die Instanz freigegeben
        await store.check_facet("ccm:replicationsource")  # freigegeben, nicht im Metadatenset
        for prop in ("cclom:title", "ccm:wwwurl", "sys:node-uuid"):
            with pytest.raises(EduSharingError) as info:
                await store.check_facet(prop)
            assert info.value.kind == "invalid_query"

    run(scenario())


def test_malformed_metadataset(tmp_path):
    repo = FakeRepo()
    repo.mds_error = httpx.Response(200, json={"widgets": "keine liste"})
    with pytest.raises(EduSharingError) as info:
        run(make(repo, tmp_path).fields())
    assert info.value.kind == "bad_response"


def test_same_named_values_of_two_vocabularies_get_told_apart(tmp_path):
    hsf = "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/"
    repo = FakeRepo()
    mds = copy.deepcopy(MDS)
    widget = next(w for w in mds["widgets"] if w["id"] == "ccm:taxonid" and w["values"])
    widget["values"] += [  # live gemessen: Die Systematik selbst steht als Wurzel im Baum
        {"id": hsf, "caption": "Destatis-Systematik der Fächergruppen"},
        {"id": hsf + "n4", "caption": "Mathematik, Naturwissenschaften", "parent": hsf},
        {"id": hsf + "n37", "caption": "Mathematik", "parent": hsf + "n4"},
        {"id": hsf + "n105", "caption": "Mathematik", "parent": hsf + "n37"},
        {"id": hsf + "n128", "caption": "Physik", "parent": hsf + "n4"}]
    repo.mds_error = httpx.Response(200, json=mds)
    http = httpx.AsyncClient(transport=repo.transport())
    inst = Instance(id="t", name="T", base_url="https://repo.example", vocab_names={
        DISC: "Schule", hsf: "Hochschule"}, vocab_levels={
        hsf: ("Fächergruppe", "Studienbereich", "Studienfach")})
    widget["values"] += [  # gleichnamig nur innerhalb der Hochschulsystematik
        {"id": hsf + "n30", "caption": "Medienwissenschaft", "parent": hsf + "n4"},
        {"id": hsf + "n302", "caption": "Medienwissenschaft", "parent": hsf + "n30"}]
    repo.mds_error = httpx.Response(200, json=mds)
    store = VocabStore(EduSharingClient(inst, http, TTLCache(ttl=900)), tmp_path, clock=Clock())

    async def scenario():
        assert await store.label("ccm:taxonid", DISC + "380") == "Mathematik (Schule)"
        assert await store.label("ccm:taxonid", hsf + "n128") == "Physik (Hochschule)"
        # zwei gleichnamige Hochschulfächer: dazu die Ebene im Baum
        assert await store.label("ccm:taxonid", hsf + "n37") == (
            "Mathematik (Hochschule, Studienbereich)")
        assert await store.label("ccm:taxonid", hsf + "n105") == (
            "Mathematik (Hochschule, Studienfach)")
        # auch ohne gleichnamiges Schulfach
        assert await store.label("ccm:taxonid", hsf + "n30") == (
            "Medienwissenschaft (Hochschule, Studienbereich)")
        assert await store.label("ccm:taxonid", hsf + "n302") == (
            "Medienwissenschaft (Hochschule, Studienfach)")
        # was nur einmal vorkommt, bleibt, wie es heißt
        assert await store.label("ccm:taxonid", hsf + "n4") == "Mathematik, Naturwissenschaften"
        # der neue Name trifft genau; der alte bleibt mehrdeutig und nennt die Auswahl
        assert (await store.resolve("ccm:taxonid", "Mathematik (Schule)")).value == DISC + "380"
        with pytest.raises(UnresolvedValue) as info:
            await store.resolve("ccm:taxonid", "Mathematik")
        assert info.value.ambiguous and {s["caption"] for s in info.value.suggestions} == {
            "Mathematik (Schule)", "Mathematik (Hochschule, Studienbereich)",
            "Mathematik (Hochschule, Studienfach)"}
        # „Sport“ gibt es zweimal im selben Vokabular: dort hilft kein Zusatz der Herkunft
        assert (await store.label("ccm:taxonid", DISC + "900")).startswith("Sport")
        # ohne Zusatz für Themen und Suche
        assert await store.plain("ccm:taxonid", DISC + "380") == "Mathematik"
        assert await store.plain("ccm:taxonid", hsf + "n4") == "Mathematik, Naturwissenschaften"

    run(scenario())


def _told(names, values):
    from types import SimpleNamespace

    from wda.edusharing.vocab import VocabValue
    store = VocabStore.__new__(VocabStore)
    store._client = SimpleNamespace(instance=SimpleNamespace(vocab_names=names,
                                                             vocab_levels={}))
    return [v.caption for v in store._told_apart(tuple(VocabValue(*v) for v in values))]


def test_telling_apart_at_the_edges():
    # der längste Anfang gewinnt, gleich in welcher Reihenfolge die Namen stehen
    for names in ({"http://v/": "Alle", "http://v/a/": "A"},
                  {"http://v/a/": "A", "http://v/": "Alle"}):
        assert _told(names, [("http://v/a/1", "M"), ("http://v/b/1", "M")]) == ["M (A)", "M (Alle)"]
    names = {"http://a/": "A", "http://b/": "B"}
    # gleiche Enden der Kennung: dann die ganze Kennung, nie zweimal derselbe Name
    out = _told(names, [("http://a/x/1", "M"), ("http://a/y/1", "M"), ("http://b/1", "M")])
    assert len(set(out)) == 3 and out[2] == "M (B)"
    # ein Wert, der schon so heißt wie der neue Name, bleibt eindeutig
    out = _told(names, [("http://a/1", "M"), ("http://b/1", "M"), ("http://a/2", "M (A)")])
    assert len(set(out)) == 3 and out[2] == "M (A)"
    # ein Wert ohne benanntes Vokabular bleibt, wie er heißt
    assert _told(names, [("http://a/1", "M"), ("http://c/1", "M")]) == ["M (A)", "M"]
    # gleichnamig im selben Vokabular: die Ebene, ohne Namen der Ebenen als Zahl
    assert _told(names, [("http://a/1", "M"), ("http://a/2", "M", "http://a/1")]) == [
        "M (A, Ebene 1)", "M (A, Ebene 2)"]


def test_telling_apart_stays_fast_with_many_same_named_values():
    import time
    names = {"http://a/": "A", "http://b/": "B"}
    values = [(f"http://{'ab'[n % 2]}/{n}", "M") for n in range(5000)]
    started = time.monotonic()
    assert len(set(_told(names, values))) == 5000
    assert time.monotonic() - started < 2


def test_instances_name_their_vocabularies():
    from wda.instances import PRESETS
    names = PRESETS[0].vocab_names
    assert names["http://w3id.org/openeduhub/vocabs/discipline/"] == "Schule"
    assert "Hochschule" in names.values() and PRESETS[1].vocab_names == names
    assert Instance(id="x", name="X", base_url="https://r.example").vocab_names == {}
    # die Suche übernimmt nur Felder ihres Formulars; nur gemessene Instanzen nennen sie
    assert "ccm:taxonid" in PRESETS[0].search_fields and PRESETS[1].search_fields == ()
    assert "ccm:replicationsource" not in PRESETS[0].search_fields
    assert PRESETS[0].public()["search_fields"] == list(PRESETS[0].search_fields) or \
        tuple(PRESETS[0].public()["search_fields"]) == PRESETS[0].search_fields
    with pytest.raises(ValueError):
        Instance(id="x", name="X", base_url="https://r.example", vocab_names={"a": "b" * 60})
