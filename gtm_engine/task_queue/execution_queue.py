# Backward compatibility proxy for V3 task_queue imports
from gtm_engine.queues.queue_provider import queue_provider

class CompatibilityExecutionQueue:
    def __getattr__(self, name):
        # Delegate all calls to the active provider instance
        return getattr(queue_provider.get_queue(), name)

    @staticmethod
    def make_key(*args, **kwargs):
        from gtm_engine.queues.queue_adapters import BaseQueueAdapter
        return BaseQueueAdapter.make_key(*args, **kwargs)

ExecutionQueue = CompatibilityExecutionQueue
execution_queue = CompatibilityExecutionQueue()
