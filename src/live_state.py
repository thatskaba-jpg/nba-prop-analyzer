"""live_state.py — tiny process-wide state that background threads can write.

Streamlit's cache_resource does NOT work from the worker threads the dashboard
uses for deadline-bounded fetches (each call there gets a fresh object), so
anything a worker thread must record lives here, in a plain module. This module
is deliberately NOT reloaded on each page run, so the values persist.
"""
PULLED = {}      # {"lines": epoch_seconds_of_last_successful_pull}
