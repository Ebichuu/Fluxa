import os


bind = f"0.0.0.0:{os.getenv('APP_PORT', '8787')}"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5
reload = False
preload_app = False
accesslog = "-"
errorlog = "-"


def post_worker_init(worker):
    from app.main import start_background_runtime

    schedulers = start_background_runtime()
    worker.log.info("background runtime started schedulers=%s", ",".join(schedulers))
