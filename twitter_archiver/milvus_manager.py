import logging
import hashlib
from typing import Optional, List, Dict, Any
from pymilvus import MilvusClient, DataType, AnnSearchRequest, RRFRanker
from datetime import datetime

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024


def get_doc_id(tweet_id: str, resource_index: Optional[int] = None) -> int:
    """Generate deterministic 64-bit signed integer ID for Milvus."""
    idx_str = "none" if resource_index is None else str(resource_index)
    key = f"{tweet_id}:{idx_str}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    doc_id = int.from_bytes(digest[:8], byteorder="big", signed=False)
    doc_id = doc_id & 0x7FFFFFFFFFFFFFFF
    return doc_id


class MilvusManager:
    """Manager for Milvus vector database with hybrid search support."""

    def __init__(self, uri: str):
        self.client = MilvusClient(uri)
        self.collections = {
            "bookmarks": "twitter_bookmarks",
            "likes": "twitter_likes",
        }

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
        from pymilvus import CollectionSchema, FieldSchema, Function, FunctionType

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
            FieldSchema(name="tweet_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="media_type", dtype=DataType.VARCHAR, max_length=16),
            FieldSchema(name="resource_index", dtype=DataType.INT64),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535, enable_analyzer=True),
            FieldSchema(name="text_bm25", dtype=DataType.SPARSE_FLOAT_VECTOR),
            FieldSchema(name="tweet_text", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="username", dtype=DataType.VARCHAR, max_length=256),
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
        tweet_id: str,
        embedding: List[float],
        text: str,
        media_type: str,
        resource_index: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        try:
            collection_name = self.collections.get(category)
            if not collection_name:
                logger.error(f"Invalid category: {category}")
                return False

            doc_id = get_doc_id(tweet_id, resource_index)

            data = {
                "id": doc_id,
                "embedding": embedding,
                "text": text[:65000] if text else "",
                "tweet_id": tweet_id,
                "media_type": media_type[:16] if media_type else "text",
                "resource_index": resource_index if resource_index is not None else -1,
                "created_at": datetime.now().isoformat(),
                "tweet_text": (metadata.get("tweet_text") or "")[:65000] if metadata else "",
                "username": (metadata.get("username") or "")[:256] if metadata else "",
            }

            self.client.insert(collection_name=collection_name, data=[data])
            logger.debug(f"Inserted embedding for tweet_id={tweet_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to insert embedding: {e}")
            return False

    def hybrid_search(
        self,
        category: str,
        query_embedding: List[float],
        query_text: str,
        limit: int = 50,
        rrf_k: int = 60,
    ) -> List[Dict[str, Any]]:
        try:
            collection_name = self.collections.get(category)
            if not collection_name:
                return []

            dense_req = AnnSearchRequest(
                data=[query_embedding],
                anns_field="embedding",
                param={"metric_type": "COSINE"},
                limit=limit,
            )

            sparse_req = AnnSearchRequest(
                data=[query_text],
                anns_field="text_bm25",
                param={"metric_type": "BM25"},
                limit=limit,
            )

            ranker = RRFRanker(k=rrf_k)

            results = self.client.hybrid_search(
                collection_name=collection_name,
                reqs=[dense_req, sparse_req],
                ranker=ranker,
                limit=limit,
                output_fields=["tweet_id", "media_type", "resource_index", "tweet_text", "username", "text"],
            )

            return [
                {
                    "id": hit["id"],
                    "score": float(hit["distance"]),
                    "tweet_id": hit["entity"].get("tweet_id"),
                    "media_type": hit["entity"].get("media_type"),
                    "resource_index": hit["entity"].get("resource_index"),
                    "tweet_text": hit["entity"].get("tweet_text"),
                    "username": hit["entity"].get("username"),
                    "text": hit["entity"].get("text"),
                }
                for hit in results[0]
            ] if results else []

        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            return []

    def close(self):
        if hasattr(self.client, "close"):
            self.client.close()
