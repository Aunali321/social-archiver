import hashlib
import logging
from datetime import datetime
from typing import Any

from pymilvus import AnnSearchRequest, CollectionSchema, DataType, Function, FunctionType, FieldSchema, MilvusClient, RRFRanker

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024


def get_doc_id(item_id: str, resource_index: int | None = None) -> int:
    """Deterministic 64-bit signed ID for a (item_id, resource_index) pair."""
    key = f"{item_id}:{'none' if resource_index is None else resource_index}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) & 0x7FFFFFFFFFFFFFFF


class MilvusManager:
    """Hybrid (dense + BM25) vector store. `collections` maps category -> Milvus
    collection name; each platform instance supplies its own category set."""

    def __init__(self, uri: str, collections: dict[str, str]):
        self.client = MilvusClient(uri)
        self.collections = collections

    def initialize_collections(self, recreate: bool = False):
        for category, collection_name in self.collections.items():
            if self.client.has_collection(collection_name):
                if recreate:
                    logger.info(f"Dropping collection: {collection_name}")
                    self.client.drop_collection(collection_name)
                    self._create_collection(collection_name)
                else:
                    logger.debug(f"Collection exists: {collection_name}")
            else:
                self._create_collection(collection_name)
                logger.info(f"Created collection: {collection_name}")

    def _create_collection(self, collection_name: str):
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
            FieldSchema(name="item_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="media_type", dtype=DataType.VARCHAR, max_length=16),
            FieldSchema(name="resource_index", dtype=DataType.INT64),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535, enable_analyzer=True),
            FieldSchema(name="text_bm25", dtype=DataType.SPARSE_FLOAT_VECTOR),
            FieldSchema(name="caption", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="username", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="code", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=32),
        ]

        bm25_function = Function(
            name="text_bm25_fn",
            function_type=FunctionType.BM25,
            input_field_names=["text"],
            output_field_names=["text_bm25"],
        )

        schema = CollectionSchema(fields=fields, functions=[bm25_function], enable_dynamic_field=True)
        self.client.create_collection(collection_name=collection_name, schema=schema)

        index_params = self.client.prepare_index_params()
        index_params.add_index(field_name="embedding", index_type="FLAT", metric_type="COSINE")
        index_params.add_index(field_name="text_bm25", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        self.client.create_index(collection_name=collection_name, index_params=index_params)

    async def insert_embedding(
        self,
        category: str,
        item_id: str,
        embedding: list[float],
        text: str,
        media_type: str,
        resource_index: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        collection_name = self.collections.get(category)
        if not collection_name:
            logger.error(f"Invalid category: {category}")
            return False

        try:
            data = {
                "id": get_doc_id(item_id, resource_index),
                "embedding": embedding,
                "text": (text or "")[:65000],
                "item_id": item_id,
                "media_type": media_type[:16] if media_type else "text",
                "resource_index": resource_index if resource_index is not None else -1,
                "created_at": datetime.now().isoformat(),
                "caption": (metadata.get("caption") or "")[:65000] if metadata else "",
                "username": (metadata.get("username") or "")[:256] if metadata else "",
                "code": (metadata.get("code") or "")[:64] if metadata else "",
            }
            self.client.insert(collection_name=collection_name, data=[data])
            logger.debug(f"Inserted embedding for item_id={item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to insert embedding: {e}")
            return False

    def hybrid_search(
        self,
        category: str,
        query_embedding: list[float],
        query_text: str,
        limit: int = 50,
        rrf_k: int = 60,
    ) -> list[dict[str, Any]]:
        collection_name = self.collections.get(category)
        if not collection_name:
            logger.error(f"Invalid category: {category}")
            return []

        try:
            dense_req = AnnSearchRequest(
                data=[query_embedding], anns_field="embedding", param={"metric_type": "COSINE"}, limit=limit
            )
            sparse_req = AnnSearchRequest(
                data=[query_text], anns_field="text_bm25", param={"metric_type": "BM25"}, limit=limit
            )

            results = self.client.hybrid_search(
                collection_name=collection_name,
                reqs=[dense_req, sparse_req],
                ranker=RRFRanker(k=rrf_k),
                limit=limit,
                output_fields=["item_id", "media_type", "resource_index", "caption", "username", "code", "text"],
            )

            if not results:
                return []
            return [
                {
                    "id": hit["id"],
                    "score": float(hit["distance"]),
                    "item_id": hit["entity"].get("item_id"),
                    "media_type": hit["entity"].get("media_type"),
                    "resource_index": hit["entity"].get("resource_index"),
                    "caption": hit["entity"].get("caption"),
                    "username": hit["entity"].get("username"),
                    "code": hit["entity"].get("code"),
                    "text": hit["entity"].get("text"),
                }
                for hit in results[0]
            ]
        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            return []

    async def delete_by_item_id(self, category: str, item_id: str) -> bool:
        collection_name = self.collections.get(category)
        if not collection_name:
            return False
        try:
            self.client.delete(collection_name=collection_name, filter=f'item_id == "{item_id}"')
            return True
        except Exception as e:
            logger.error(f"Failed to delete embeddings: {e}")
            return False

    def close(self):
        if hasattr(self.client, "close"):
            self.client.close()
