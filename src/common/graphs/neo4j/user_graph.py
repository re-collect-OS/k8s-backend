# -*- coding: utf-8 -*-
from datetime import datetime
from textwrap import dedent
from typing import Any, LiteralString, cast
from uuid import UUID

import neo4j
import neo4j.time
from loguru import logger
from neo4j.graph import Node as Neo4jNode
from neo4j.graph import Relationship

from ...text import EmbedFunc
from ..graph import Edge, EdgeType, GraphCollection, GraphCollections, Node, NodeType
from ..user_graph import UserGraph

# ENTITY_TYPES = {
#    "PERSON",
#    "NORP",
#    "FAC",
#    "ORG",
#    "GPE",
#    "LOC",
#    "PRODUCT",
#    "EVENT",
#    "WORK_OF_ART",
# }


class Neo4jUserGraph(
    UserGraph[NodeType, EdgeType],
):
    def __init__(
        self,
        client: neo4j.Driver,
        graph_collection: GraphCollection = GraphCollections.Graph_v20231222,
    ) -> None:
        self._client = client
        self._graph_collection = graph_collection
        self._initialize_database()

    def _initialize_database(self):
        with self._client.session(database="neo4j") as session:
            # Create indexes and constraints
            index_queries = [
                "CREATE INDEX IF NOT EXISTS FOR (n:Artifact) ON (n.id);",
                """CREATE VECTOR INDEX `node_embedding_v1` IF NOT EXISTS
                   FOR (n:Artifact) ON (n.node_embedding)
                   OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};""",
            ]
            for query in index_queries:
                self._run_query(session, query)

    def add_edge(
        self,
        start_node: NodeType,
        end_node: NodeType,
        edge: EdgeType,
    ) -> None:
        # don't allow self-loops
        assert start_node != end_node
        # sanity check
        assert start_node.user_id == end_node.user_id == edge.user_id

        with self._client as driver, driver.session(database="neo4j") as session:
            properties = edge.model_dump(mode="json")
            directed = properties.pop("directed")
            relationship_type = properties.pop("relationship_type")

            if directed:
                connector_ascii = "->"
            else:
                connector_ascii = "-"

            query_str = (
                "MATCH (startNode), (endNode) "
                f"WHERE startNode.id='{start_node.id}' AND endNode.id='{end_node.id}' "
                f"MERGE (startNode)-[r:{relationship_type}]{connector_ascii}(endNode) "
                "SET r += $properties;"
            )
            self._run_query(session, query_str, parameters={"properties": properties})

    def bulk_upsert_and_connect_nodes(
        self,
        data: list[dict[str, Any]],
    ):
        query_template = """
            UNWIND $data as row
            MERGE (n:{node_type} {{id: row.node.id}})
            ON CREATE SET n += row.node.properties
            ON MATCH SET n += row.node.properties
            WITH n, row
            UNWIND row.relationships as rel
            MERGE (target:{target_node_type} {{id: rel.target_node_id}})
            ON CREATE SET target += rel.target_properties
            MERGE (n)-[r:{edge_type}]->(target)
            SET r += rel.edge_properties
        """

        with self._client.session(database="neo4j") as session:
            # Generate and run the dynamic query for each row in the data
            for row in data:
                formatted_query = query_template.format(
                    node_type=row["node"]["node_type"],
                    target_node_type=row["relationships"][0]["target_node_type"],
                    edge_type=row["relationships"][0]["edge_type"],
                )
                self._run_query(session, formatted_query, parameters={"data": [row]})

    def add_node(self, node: NodeType):
        """
        NOTE: https://neo4j.com/docs/cypher-manual/current/indexes-for-vector-search/
            Cypher stores the provided LIST<FLOAT> as a primitive array of IEEE 754[2] double precision values
            via MATCH .. SET. This takes up almost twice as much space compared to the alternative method
            using the db.create.setNodeVectorProperty procedure like so:
            MATCH (n:Node {id: $id})
            CALL db.create.setNodeVectorProperty(n, 'propertyKey', $vector)
            RETURN node
        """
        with self._client.session(database="neo4j") as session:
            properties = node.model_dump(mode="json")

            embedding_vector = properties.pop(
                "embedding_vector", None
            )  # don't use SET, see note above
            label: str = properties.pop("node_type")

            query_str = f"MATCH (n:{label}) WHERE n.id='{node.id}' RETURN COUNT(n) AS nodeCount;"
            result = self._run_query(session, query_str)
            node_count = result.single(strict=True)["nodeCount"]

            if not node_count:
                # create node without embedding vector property
                query_str = f"CREATE (n:{label} $properties) RETURN n;"
                self._run_query(
                    session, query_str, parameters={"properties": properties}
                )

            # if the node exists, update the properties (not all properties need to be used
            # as input when calculating the unique id, so this does have an effect)
            else:
                query_str = f"MATCH (n:{label}) WHERE n.id='{id}' SET n += $properties;"
                self._run_query(
                    session,
                    query_str,
                    parameters={"properties": properties},
                )

            if embedding_vector is not None:
                # add embedding vector as IEEE 754[2] single precision
                query_str = (
                    f"MATCH (n:{label}) WHERE n.id='{id}' "
                    "CALL db.create.setNodeVectorProperty(n, 'embedding_vector', $vector);"
                )
                self._run_query(
                    session,
                    query_str,
                    parameters={"embedding_vector": embedding_vector},
                )

            if label == "Artifact":
                # redo READ_NEXT relationships between artifact nodes
                # NOTE it looks like it is very fast to do a full delete and redo
                # instead of more complex logic
                delete_query_str = """
                WITH $user_id AS userId
                MATCH (a:Artifact {user_id: userId})-[r:READ_NEXT]->()
                DELETE r
                """
                self._run_query(
                    session,
                    delete_query_str,
                    parameters={
                        "user_id": str(node.user_id),
                    },
                )
                # redo after full delete
                create_query_str = """
                WITH $user_id AS userId
                MATCH (a:Artifact)
                WHERE a.last_visited <> '1970-01-01T00:00:00Z' AND a.user_id = userId
                WITH a
                ORDER BY a.last_visited ASC
                WITH collect(DISTINCT a) AS artifacts
                // Unwind the list and create relationships in order
                UNWIND range(0, size(artifacts) - 2) AS i
                WITH artifacts[i] AS prec, artifacts[i+1] AS next
                CREATE (prec)-[:READ_NEXT]->(next)
                """
                self._run_query(
                    session,
                    create_query_str,
                    parameters={
                        "user_id": str(node.user_id),
                    },
                )

    def get_node_by_id(self, id: UUID) -> Node:
        with self._client as driver, driver.session(database="neo4j") as session:
            query_str = f"MATCH (n) WHERE n.id = '{id}' RETURN n;"

            result = self._run_query(
                session,
                query_str,
            )
            node = result.single(strict=True)["n"]

            return Neo4jUserGraph._map_node(node)

    def get_edge_by_id(self, id: UUID) -> Edge:
        # TODO edge is assumed to be directed
        with self._client as driver, driver.session(database="neo4j") as session:
            query_str = f"MATCH ()-[r]->() WHERE r.id = '{id}' RETURN r;"

            result = self._run_query(
                session,
                query_str,
            )
            edge = result.single(strict=True)["r"]

            return Neo4jUserGraph._map_edge(edge)

    def get_nodes_by_user_id(
        self, user_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[NodeType]:
        with self._client as driver, driver.session(database="neo4j") as session:
            query_str = (
                f"MATCH (n) WHERE n.user_id = '{user_id}' RETURN n "
                f"ORDER BY ID(n) SKIP {offset} LIMIT {limit};"
            )

            results = self._run_query(
                session,
                query_str,
            )
            nodes = [result["n"] for result in results]
            nodes = [Neo4jUserGraph._map_node(n) for n in nodes]

            return nodes

    def get_nodes_by_property_value(
        self, property: str, value: Any, offset: int = 0, limit: int = 50
    ) -> list[NodeType]:
        with self._client as driver, driver.session(database="neo4j") as session:
            query_str = (
                f"MATCH (n) WHERE n.{property}='{value}' RETURN n "
                f"ORDER BY ID(n) SKIP {offset} LIMIT {limit};"
            )

            results = self._run_query(
                session,
                query_str,
            )
            nodes = [result["n"] for result in results]
            nodes = [Neo4jUserGraph._map_node(n) for n in nodes]

            return nodes

    def get_nodes_by_property_value_by_label(
        self,
        property: str,
        value: Any,
        label: str = "Artifact",
        offset: int = 0,
        limit: int = 50,
    ) -> list[NodeType]:
        with self._client as driver, driver.session(database="neo4j") as session:
            query_str = (
                f"MATCH (n:{label}) WHERE n.{property}='{value}' RETURN n "
                f"ORDER BY ID(n) SKIP {offset} LIMIT {limit};"
            )

            results = self._run_query(
                session,
                query_str,
            )
            nodes = [result["n"] for result in results]
            nodes = [Neo4jUserGraph._map_node(n) for n in nodes]

            return nodes

    def nodes(
        self, label: str | None = None, offset: int = 0, limit: int = 50
    ) -> list[NodeType]:
        with self._client as driver, driver.session(database="neo4j") as session:
            if label is None:
                query_str = (
                    f"MATCH (n) RETURN n ORDER BY ID(n) SKIP {offset} LIMIT {limit};"
                )
            else:
                query_str = f"MATCH (n:{label}) RETURN n ORDER BY ID(n) SKIP {offset} LIMIT {limit};"

            results = self._run_query(
                session,
                query_str,
            )
            nodes = [result["n"] for result in results]
            nodes = [Neo4jUserGraph._map_node(n) for n in nodes]

            return nodes

    def get_edges_by_user_id(
        self, user_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[EdgeType]:
        with self._client as driver, driver.session(database="neo4j") as session:
            query_str = (
                f"MATCH ()-[r]-() WHERE r.user_id = '{user_id}' RETURN r "
                f"ORDER BY ID(r) SKIP {offset} LIMIT {limit};"
            )

            results = self._run_query(
                session,
                query_str,
            )
            edges = [result["r"] for result in results]
            edges = [Neo4jUserGraph._map_edge(e) for e in edges]

            return edges

    def edges(self, offset: int = 0, limit: int = 50) -> list[EdgeType]:
        # NOTE: this will double count directed edges as two undirected edges
        with self._client as driver, driver.session(database="neo4j") as session:
            query_str = (
                f"MATCH ()-[r]-() RETURN r "
                f"ORDER BY ID(r) SKIP {offset} LIMIT {limit};"
            )

            results = self._run_query(
                session,
                query_str,
            )
            edges = [result["r"] for result in results]
            edges = [Neo4jUserGraph._map_edge(e) for e in edges]

            return edges

    def count_nodes(self, label: str | None = None) -> int:
        with self._client as driver, driver.session(database="neo4j") as session:
            if label is None:
                query_str = "MATCH (n) RETURN COUNT(n) AS nodeCount;"
            else:
                query_str = f"MATCH (n:{label}) RETURN COUNT(n) AS nodeCount;"

            return self._run_query(session, query_str).single(strict=True)["nodeCount"]

    def count_edges(self, relationship_type: str | None = None) -> int:
        with self._client as driver, driver.session(database="neo4j") as session:
            if relationship_type is None:
                query_str = "MATCH ()-[r]->() RETURN COUNT(r) AS edgeCount;"
            else:
                query_str = f"MATCH ()-[r:{relationship_type}]->() RETURN COUNT(r) AS edgeCount;"

            return self._run_query(session, query_str).single(strict=True)["edgeCount"]

    def count_nodes_with_property_value(
        self, label: str, property_key: str, property_value: Any
    ) -> int:
        with self._client as driver, driver.session() as session:
            # Neo4j uses the ISO 8601 date and time format
            # for representing datetime values
            if type(property_value) == datetime:
                property_value = property_value.isoformat()
            if type(property_value) == UUID:
                property_value = str(property_value)

            query_str = (
                f"MATCH (n:{label}) WHERE n.{property_key} = $property_value "
                "RETURN COUNT(n) AS nodeCount;"
            )

            result = self._run_query(
                session,
                query_str,
                parameters={
                    "label": label,
                    "property_key": property_key,
                    "property_value": property_value,
                },
            )

            return result.single(strict=True)["nodeCount"]

    def delete_nodes_with_property_value(
        self, label: str, property_key: str, property_value: Any
    ):
        with self._client as driver, driver.session() as session:
            # Neo4j uses the ISO 8601 date and time format
            # for representing datetime values
            if type(property_value) == datetime:
                property_value = property_value.isoformat()
            if type(property_value) == UUID:
                property_value = str(property_value)
            # Delete all nodes with a certain property value, also detaches
            # and deletes all relationships connected to that node.
            query_str = (
                f"MATCH (n:{label} {{{property_key}: $property_value}}) "
                "DETACH DELETE n;"
            )

            self._run_query(
                session,
                query_str,
                parameters={
                    "label": label,
                    "property_key": property_key,
                    "property_value": property_value,
                },
            )

    def delete_nodes_with_label(self, label: str):
        with self._client as driver, driver.session() as session:
            # detaches and deletes all relationships connected to that node.
            query_str = f"MATCH (n:{label}) DETACH DELETE n;"

            self._run_query(session, query_str, parameters={"label": label})

    @staticmethod
    def _map_node(neo4j_node: Neo4jNode) -> Node:
        last_visited = neo4j_node["last_visited"]

        if type(last_visited) == neo4j.time.DateTime:
            last_visited = datetime(
                last_visited.year,
                last_visited.month,
                last_visited.day,
                last_visited.hour,
                last_visited.minute,
                last_visited.second,
                0,  # error: DateTime has no attribute 'microsecond'
                last_visited.tzinfo,
            )

        return Node(
            id=neo4j_node["id"],
            user_id=neo4j_node["user_id"],
            artifact_id=neo4j_node["artifact_id"],
            summary=neo4j_node["summary"],
            node_type=set(neo4j_node.labels).pop(),
            node_name=neo4j_node["node_name"],
            node_embedding=neo4j_node["node_embedding"],
            last_visited=last_visited,
        )

    @staticmethod
    def _map_edge(neo4j_relationship: Relationship) -> Edge:
        return Edge(
            id=neo4j_relationship["id"],
            user_id=neo4j_relationship["user_id"],
            start_node_id=neo4j_relationship["start_node_id"],
            end_node_id=neo4j_relationship["end_node_id"],
            relationship_type=neo4j_relationship.type,
        )

    @staticmethod
    def _run_query(
        session: neo4j.Session, query: str, parameters: dict[str, Any] | None = None
    ) -> neo4j.Result:
        query = cast(LiteralString, dedent(query).strip())
        try:
            result = session.run(query, parameters)
            return result
        except Exception as e:
            logger.exception("Failed to execute query: {}", query)
            raise e

    @staticmethod
    def _delete_all_nodes_and_relationships(tx: neo4j.ManagedTransaction) -> None:
        # Delete all relationships
        tx.run("MATCH ()-[r]-() DELETE r")
        # Delete all nodes
        tx.run("MATCH (n) DELETE n")

    def delete_all(self) -> None:
        with self._client as driver, driver.session(database="neo4j") as session:
            session.execute_write(self._delete_all_nodes_and_relationships)

    @property
    def graph_collection(self) -> GraphCollection:
        return self._graph_collection
