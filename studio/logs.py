import contextvars
import logging
import os

# Short id of the HTTP request or generation job the current task is serving.
context_id = contextvars.ContextVar('context_id', default='-')


class _ContextId(logging.Filter):
    def filter(self, record):
        record.ctx = context_id.get()
        return True


def setup():
    level = getattr(logging, os.getenv('STUDIO_LOG_LEVEL', 'INFO').upper(), None)
    handler = logging.StreamHandler()
    handler.addFilter(_ContextId())
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s [%(ctx)s] %(message)s'))
    logging.basicConfig(level=level if isinstance(level, int) else logging.INFO, handlers=[handler])
