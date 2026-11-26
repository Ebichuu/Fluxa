import os

bind = f"{os.environ.get('APP_HOST', '0.0.0.0')}:{os.environ.get('APP_PORT', '8987')}"
workers = 1
threads = 4
timeout = 60
graceful_timeout = 15
accesslog = "-"
errorlog = "-"
