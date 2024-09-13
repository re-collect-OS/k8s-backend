# -*- coding: utf-8 -*-
from neo4j import Driver, GraphDatabase

from common import env


def cloud_rw_client() -> Driver:
    return GraphDatabase.driver(
        env.require_str("NEO4J_GRAPHENEDB_URL"),
        auth=(
            env.require_str("NEO4J_GRAPHENEDB_USER"),
            env.require_str("NEO4J_GRAPHENEDB_PASSWORD"),
        ),
    )
