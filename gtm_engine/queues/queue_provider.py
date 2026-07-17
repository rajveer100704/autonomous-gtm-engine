import os
from typing import Optional
from gtm_engine.utils.feature_flags import is_feature_enabled
from gtm_engine.queues.queue_adapters import BaseQueueAdapter, SQLiteQueueAdapter, RedisQueueAdapter

class QueueProvider:
    """
    Lazy-initializing provider for GTM execution queues.
    Returns SQLiteQueueAdapter or RedisQueueAdapter depending on configuration.
    """
    def __init__(self):
        self._instance: Optional[BaseQueueAdapter] = None

    def get_queue(self) -> BaseQueueAdapter:
        """Get or initialize the queue adapter instance."""
        if self._instance is None:
            use_redis = is_feature_enabled("USE_REDIS")
            redis_url = os.environ.get("REDIS_URL")

            if use_redis and redis_url:
                try:
                    self._instance = RedisQueueAdapter(redis_url)
                except Exception as e:
                    # Log fallback on connection failure
                    import logging
                    logging.getLogger("gtm.queues").warning(
                        "QueueProvider: Redis connection failed, falling back to SQLite. Error: %s", e
                    )
                    self._instance = SQLiteQueueAdapter()
            else:
                self._instance = SQLiteQueueAdapter()
        return self._instance

    def reset(self) -> None:
        """Reset the cached instance — useful during test runs to switch DB engines."""
        self._instance = None

# Global Queue Provider instance
queue_provider = QueueProvider()
