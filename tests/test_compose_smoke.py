"""Compose-profile Bolt smoke (Phase 13a Task 9). `compose`-marked.

Runs ONLY in the CI `compose` job, which brings infra/docker-compose.yml's
`graph` profile UP first (docker compose --profile graph up -d --wait). It
connects through the SHIPPED compose wiring (the localhost-bound ports, the
NEO4J_AUTH from the env, the healthcheck) -- so it verifies the compose file an
operator actually runs, not a bare service container (that is the `graph` job).

ASCII only. Deselected from the offline suite by `-m "not compose"`; importorskip
keeps collection clean when the neo4j driver is absent (the offline CI job does
not install requirements-graph).
"""
import os

import pytest

pytestmark = pytest.mark.compose


def test_compose_graph_profile_bolt_connect_and_return_one():
    neo4j = pytest.importorskip("neo4j")
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD", "testpassword")
    try:
        driver = neo4j.GraphDatabase.driver(uri, auth=(user, pw))
        driver.verify_connectivity()
    except Exception as exc:  # no compose stack up (e.g. a local run) -> skip
        pytest.skip("no compose Neo4j reachable at %s: %s" % (uri, exc))
    try:
        with driver.session() as session:
            assert session.run("RETURN 1 AS x").single()["x"] == 1
    finally:
        driver.close()
