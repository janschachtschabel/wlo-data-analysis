"""Sichert ab, dass der Testbestand die gemessenen Regeln wirklich nachbildet."""

import asyncio

import httpx

from wda.cache import TTLCache
from wda.edusharing.client import Criterion, EduSharingClient
from wda.instances import Instance

from .esdata import DISC, EC, DataRepo

M, P = DISC + "380", DISC + "460"
S1 = EC + "sekundarstufe_1"


def client_for(repo):
    return EduSharingClient(Instance(id="t", name="T", base_url="https://r.example"),
                            httpx.AsyncClient(transport=repo.transport()), TTLCache(ttl=0))


def agg(repo, criteria, facets=(), **kw):
    return asyncio.run(client_for(repo).aggregate(
        [Criterion(p, tuple(v)) for p, v in criteria], facets, **kw))


def test_or_within_and_between():
    repo = DataRepo()
    assert agg(repo, []).total == len(repo.docs) == 400
    either = agg(repo, [("ccm:taxonid", [M, P])]).total
    assert either == repo.brute(repo.has("ccm:taxonid", M, P))
    assert either < agg(repo, [("ccm:taxonid", [M])]).total + agg(
        repo, [("ccm:taxonid", [P])]).total  # mehrwertig: ODER ist nicht die Summe
    both = agg(repo, [("ccm:taxonid", [M]), ("ccm:educationalcontext", [S1])]).total
    assert both == repo.brute(lambda d: M in d["ccm:taxonid"]
                              and S1 in d["ccm:educationalcontext"])


def test_license_prefix_and_groups():
    repo = DataRepo()
    exact = repo.brute(repo.has("ccm:commonlicense_key", "CC_BY"))
    prefix = agg(repo, [("ccm:commonlicense_key", ["CC_BY"])]).total
    assert prefix == repo.brute(lambda d: any(k.startswith("CC_BY")
                                              for k in d["ccm:commonlicense_key"])) > exact
    with_key = agg(repo, [("ccm:commonlicense_key", [""])]).total
    assert with_key == repo.brute(lambda d: bool(d["ccm:commonlicense_key"]))
    oer = agg(repo, [("license", ["OPEN", "CC_BY_OPEN"])]).total
    assert oer == repo.brute(repo.has("ccm:commonlicense_key", "CC_0", "PDM", "CC_BY",
                                      "CC_BY_SA"))


def test_facet_ignores_own_filter_but_not_others():
    repo = DataRepo()
    unfiltered = agg(repo, [], ["ccm:taxonid"]).facets["ccm:taxonid"]
    own = agg(repo, [("ccm:taxonid", [M])], ["ccm:taxonid"]).facets["ccm:taxonid"]
    assert own.values == unfiltered.values
    other = agg(repo, [("ccm:educationalcontext", [S1])], ["ccm:taxonid"]).facets["ccm:taxonid"]
    counts = {v.value: v.count for v in other.values}
    assert counts[M] == repo.brute(lambda d: M in d["ccm:taxonid"]
                                   and S1 in d["ccm:educationalcontext"])


def test_facet_counts_only_documents_with_value_and_limit():
    repo = DataRepo()
    facet = agg(repo, [], ["ccm:commonlicense_key"]).facets["ccm:commonlicense_key"]
    assert sum(v.count for v in facet.values) == repo.brute(
        lambda d: bool(d["ccm:commonlicense_key"])) < 400
    small = agg(repo, [], ["ccm:commonlicense_key"], facet_limit=1)
    facet = small.facets["ccm:commonlicense_key"]
    assert len(facet.values) == 5 and facet.truncated
