import logging
import hashlib
from typing import Optional, List, Dict, Any
from pymilvus import MilvusClient, DataType, AnnSearchRequest, RRFRanker
from datetime import datetime

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768


def get_doc_id(media_pk: int, resource_index: Optional[int] = None) -> int:
    """Generate deterministic 64-bit signed integer ID for Milvus."""
    idx_str = "none" if resource_index is None else str(resource_index)
    key = f"{media_pk}:{idx_str}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    doc_id = int.from_bytes(digest[:8], byteorder="big", signed=False)
    doc_id = doc_id & 0x7FFFFFFFFFFFFFFF
    return doc_id


class MilvusManager:
    """Manager for Milvus vector database with hybrid search support."""

    def __init__(self, uri: str):
        self.client = MilvusClient(uri)
        self.collections = {
            "likes": "instagram_likes",
            "saved": "instagram_saved",
            "shared": "instagram_shared",
        }

    def initialize_collections(self, recreate: bool = False):
        """Create collections with hybrid search schema."""
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
        """Create collection with dense + BM25 fields for hybrid search."""
        from pymilvus import CollectionSchema, FieldSchema, Function, FunctionType

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
            FieldSchema(name="media_pk", dtype=DataType.INT64),
            FieldSchema(name="media_type", dtype=DataType.INT64),
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
        
        # Create indexes using prepare_index_params
        index_params = self.client.prepare_index_params()
        index_params.add_index(field_name="embedding", index_type="FLAT", metric_type="COSINE")
        index_params.add_index(field_name="text_bm25", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        self.client.create_index(collection_name=collection_name, index_params=index_params)

    async def insert_embedding(
        self,
        category: str,
        media_pk: int,
        embedding: List[float],
        text: str,
        media_type: int,
        resource_index: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Insert embedding with searchable text for hybrid search."""
        try:
            collection_name = self.collections.get(category)
            if not collection_name:
                logger.error(f"Invalid category: {category}")
                return False

            doc_id = get_doc_id(media_pk, resource_index)

            data = {
                "id": doc_id,
                "embedding": embedding,
                "text": text[:65000] if text else "",
                "media_pk": media_pk,
                "media_type": media_type,
                "resource_index": resource_index if resource_index is not None else -1,
                "created_at": datetime.now().isoformat(),
                "caption": (metadata.get("caption") or "")[:65000] if metadata else "",
                "username": (metadata.get("username") or "")[:256] if metadata else "",
                "code": (metadata.get("code") or "")[:64] if metadata else "",
            }

            self.client.insert(collection_name=collection_name, data=[data])
            logger.debug(f"Inserted embedding for media_pk={media_pk}")
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
        """Hybrid search combining dense (semantic) and sparse (BM25) with RRF."""
        try:
            collection_name = self.collections.get(category)
            if not collection_name:
                logger.error(f"Invalid category: {category}")
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
                output_fields=["media_pk", "media_type", "resource_index", "caption", "username", "code", "text"],
            )

            return [
                {
                    "id": hit["id"],
                    "score": float(hit["distance"]),
                    "media_pk": hit["entity"].get("media_pk"),
                    "media_type": hit["entity"].get("media_type"),
                    "resource_index": hit["entity"].get("resource_index"),
                    "caption": hit["entity"].get("caption"),
                    "username": hit["entity"].get("username"),
                    "code": hit["entity"].get("code"),
                    "text": hit["entity"].get("text"),
                }
                for hit in results[0]
            ] if results else []

        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            return []

    async def search(
        self,
        category: str,
        query_embedding: List[float],
        limit: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Pure dense vector search."""
        try:
            collection_name = self.collections.get(category)
            if not collection_name:
                return []

            search_params = {
                "collection_name": collection_name,
                "data": [query_embedding],
                "limit": limit,
                "output_fields": ["media_pk", "media_type", "resource_index", "caption", "username", "code"],
            }
            if filter_expr:
                search_params["filter"] = filter_expr
            
            results = self.client.search(**search_params)

            return results[0] if results else []

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    async def delete_by_media_pk(self, category: str, media_pk: int) -> bool:
        """Delete all embeddings for a media_pk."""
        try:
            collection_name = self.collections.get(category)
            if not collection_name:
                return False

            self.client.delete(collection_name=collection_name, filter=f"media_pk == {media_pk}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete embeddings: {e}")
            return False

    def close(self):
        if hasattr(self.client, "close"):
            self.client.close()
