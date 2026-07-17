# Backward compatibility proxy for V3 task_queue dead letter queue
from gtm_engine.queues.dead_letter_queue import (
    dead_letter_queue, DeadLetterQueue, dead_letter_queue_table, init_dlq
)
