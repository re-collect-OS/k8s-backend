# -*- coding: utf-8 -*-
from neo4j import Driver, GraphDatabase

from . import env


def neo4j_client_from_env() -> Driver:
    return GraphDatabase.driver(
        env.require_str("NEO4J_GRAPHENEDB_URL"),
        auth=(
            env.require_str("NEO4J_GRAPHENEDB_USER"),
            env.require_str("NEO4J_GRAPHENEDB_PASSWORD"),
        ),
    )
