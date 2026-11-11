# newscentral/supervisor.py
# Starts the worker process and restarts it after a crash. Completed in Task 4.

EXIT_SCHEMA = 3    # worker exit code: database and code disagree on the schema; do not restart
