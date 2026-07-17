# gtm_engine/queue/__init__.py
# NOTE: Do NOT import execution_queue at package level.
# Python's stdlib has a 'queue' module; importing here breaks langsmith's
# pytest plugin which does: import queue (stdlib).
# Import explicitly in calling code:
#   from gtm_engine.task_queue.execution_queue import execution_queue
