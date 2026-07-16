# Embeddings Support

The Instagram archiver supports generating embeddings for archived media using OpenRouter API. This enables semantic search across your archived Instagram content using natural language queries or images.

## Features

- **VLM-Powered Descriptions**: Uses Gemini Flash to describe images and transcribe video audio
- **Text Embeddings**: Uses jina-embeddings-v5-omni-small locally to embed descriptions
- **Semantic Search**: Find posts using natural language queries
- **Image Similarity**: Search using reference images
- **Album Support**: Each item in an Instagram album gets its own embedding
- **Vector Database**: Uses Milvus Lite for efficient similarity search
- **Parallel Processing**: Embeddings are generated in parallel with Telegram uploads
- **Auto-cleanup**: Downloaded files are deleted after both upload and embedding complete

## Requirements

1. **OpenRouter API Key**: Get one from [openrouter.ai](https://openrouter.ai)
2. **Python Dependencies**: `pymilvus>=2.4.0` (automatically installed)

## Setup

### 1. Get OpenRouter API Key

1. Go to [openrouter.ai](https://openrouter.ai)
2. Sign up and add credits
3. Create an API key

### 2. Enable Embeddings in .env

```env
# Embedding Configuration
EMBEDDING_ENABLED=true
OPENROUTER_API_KEY=sk-or-v1-your-api-key-here

# Optional: Customize models (these are the defaults)
VLM_MODEL=google/gemini-3-flash-preview
EMBEDDING_TIMEOUT=180

# Milvus database path
INSTAGRAM_MILVUS_URI=./milvus_instagram.db
```

### 3. Run the Archiver

```bash
uv run python -m social_archiver.platforms.instagram
```

The archiver will now:
1. Download Instagram media
2. Upload to Telegram (in parallel with embedding generation)
3. Describe media using Gemini Flash VLM
4. Generate text embeddings and store in Milvus
5. Clean up downloaded files after both operations complete

## Usage

### Search by Text Query

```bash
# Search in likes collection
python search_embeddings.py search "beach sunset photos" --category likes --limit 10

# Search in saved collection
python search_embeddings.py search "food photography" --category saved --limit 5
```

### Search by Image

```bash
# Find similar images
python search_embeddings.py image /path/to/query_image.jpg --category likes --limit 10
```

### View Statistics

```bash
python search_embeddings.py stats
```

## How It Works

### Embedding Generation Workflow

1. **Download**: Media is downloaded from Instagram
2. **Parallel Processing**:
   - Telegram upload starts
   - Embedding generation starts (simultaneously)
3. **VLM Description** (via Gemini Flash):
   - For images: Detailed visual description
   - For videos: Visual description + full audio transcription
4. **Text Embedding** (via jina-embeddings-v5-omni-small, local):
   - Combines Instagram caption with VLM description
   - Generates 1024-dimension embedding
5. **Storage**:
   - Embeddings stored in Milvus Lite (separate collections per category)
   - Metadata tracked in SQLite database
6. **Cleanup**: Files deleted after both upload and embedding complete

### Database Schema

The `processed_media` table includes:
- `embedded_at`: Timestamp when embedding was generated
- `embedding_status`: "completed", "failed", or NULL

### Milvus Collections

Three separate collections are created:
- `instagram_likes`: Embeddings for liked posts
- `instagram_saved`: Embeddings for saved posts
- `instagram_shared`: Embeddings for DM-shared posts

Each document includes:
- `id`: Unique ID (hash of media_pk and resource_index)
- `embedding`: Vector (1024 dimensions)
- `media_pk`: Instagram media primary key
- `media_type`: 1=photo, 2=video, 8=album
- `resource_index`: Index for album items (NULL for single media)
- `caption`: Post caption
- `username`: Author username
- `code`: Instagram short code
- `created_at`: Embedding generation timestamp

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_ENABLED` | `false` | Enable/disable embedding generation |
| `OPENROUTER_API_KEY` | - | Your OpenRouter API key (required) |
| `VLM_MODEL` | `google/gemini-3-flash-preview` | Vision-language model for descriptions |
| `EMBEDDING_TIMEOUT` | `180` | HTTP timeout for API requests (seconds) |

Text embeddings run locally with `jinaai/jina-embeddings-v5-omni-small` (1024 dimensions, retrieval task).
| `INSTAGRAM_MILVUS_URI` | `./milvus_instagram.db` | Milvus Lite database path |

## Cost Estimation

OpenRouter pricing (as of writing):
- **Gemini Flash**: ~$0.10/1M input tokens, ~$0.40/1M output tokens
- **Text embedding**: free, runs locally

Typical cost per media item:
- Images: ~$0.0001-0.0005 per image
- Videos: Higher due to longer processing, varies by length

## Error Handling

- If embedding generation fails, the media is still uploaded to Telegram
- Failed embeddings are tracked in the database (`embedding_status='failed'`)
- Error notifications are sent to your Telegram error chat
- Automatic retry with exponential backoff (3 attempts max)

## Troubleshooting

### API Key Issues

```
Error: OPENROUTER_API_KEY is not set
```

Solution: Make sure `OPENROUTER_API_KEY` is set in your `.env` file

### Rate Limiting

```
Rate limited, waiting Xs
```

Solution: The system automatically retries with exponential backoff. If persistent, check your OpenRouter rate limits.

### Timeout Errors

Solution: Increase `EMBEDDING_TIMEOUT` in `.env` (default is 180 seconds)

### Dimension Mismatch

If you're changing from a different embedding model, you need to recreate the Milvus collections:

```python
# In Python or add --recreate-embeddings flag (TODO)
from social_archiver.core.milvus_manager import MilvusManager
manager = MilvusManager("./milvus_instagram.db", collections)
manager.initialize_collections(recreate=True)
```

## Backfill Failed/Missing Embeddings

Embedding is an independent, resumable job. Run it any time to process whatever
is still pending; add `--retry-failed` to also retry items that previously failed:

```bash
uv run python -m social_archiver.platforms.instagram embed
uv run python -m social_archiver.platforms.instagram embed --retry-failed
uv run python -m social_archiver.platforms.twitter embed --retry-failed
```

The job finds archived items without embeddings, re-downloads media from the
stored URLs when the local files are gone, generates descriptions and
embeddings, and records per-item status (`embed_status`, `embed_error`) in the
database.

## Future Enhancements

- [x] Backfill embeddings for historical media
- [ ] Web UI for semantic search
- [ ] Telegram bot integration for in-chat search
- [ ] Support for custom instructions per search query
- [ ] Hybrid search (text + image query combined)
- [ ] Content clustering and recommendations
