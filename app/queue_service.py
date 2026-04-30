import os
import json
import time
import threading
from collections import defaultdict, deque
from threading import Lock, Event

# Simple in-memory prefetch pool with optional on-disk persistence.
# Designed to be started from create_app(app) where Flask app context exists.

MIN_POOL = 2          # When pool for a topic falls below this, prefetch
PREFETCH_BATCH = 3    # How many exercises to fetch per fill
MAX_POOL = 8          # Maximum held per topic
SLEEP_INTERVAL = 10   # Seconds between background checks
CACHE_FILE = os.path.join(os.path.dirname(__file__), 'exercise_cache.json')

_pools: dict[str, deque] = defaultdict(deque)
_lock = Lock()
_stop_event: Event | None = None
_thread: threading.Thread | None = None


def _pool_key(topic: str, mode: str = "random", subtopic_focus: str | None = None) -> str:
    focus = (subtopic_focus or "").strip().lower()
    return f"{topic}::{mode}::{focus}"


def _load_cache():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with _lock:
                for topic, items in data.items():
                    filtered_items = [
                        item for item in items
                        if isinstance(item, dict) and item.get('questions') and item.get('source') != 'fallback'
                    ]
                    if filtered_items:
                        _pools[topic] = deque(filtered_items)
    except Exception:
        # Ignore corrupt cache
        pass


def _save_cache():
    try:
        with _lock:
            data = {
                t: [item for item in dq if isinstance(item, dict) and item.get('source') != 'fallback']
                for t, dq in _pools.items() if dq
            }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _safe_generate(
    topic: str,
    level: str = "Intermediate",
    exercise_type: str = "Multiple Choice",
    streak_days: int | None = None,
    mode: str = "random",
    topic_type: str | None = None,
    subtopic_focus: str | None = None,
):
    # Import lazily to avoid circular imports when module is imported.
    try:
        from app.llm_service import generate_exercise, fallback_exercise
    except Exception:
        return None
    try:
        ex = generate_exercise(
            topic,
            level=level,
            exercise_type=exercise_type,
            streak_days=streak_days,
            mode=mode,
            topic_type=topic_type,
            subtopic_focus=subtopic_focus,
        )
        if not isinstance(ex, dict) or not ex.get('questions'):
            return fallback_exercise(topic)
        return ex
    except Exception as exc:
        try:
            from flask import current_app
            current_app.logger.warning("[queue_service] Generation failed for topic=%r: %s", topic, exc)
        except Exception:
            pass
        try:
            return fallback_exercise(topic)
        except Exception:
            return None


def start_prefetcher(app):
    """Start background thread that keeps a small pool of generated exercises per topic.
    Must be called from create_app(app) so an app context is available.
    """
    global _stop_event, _thread
    if _thread and _thread.is_alive():
        return

    _stop_event = Event()

    def worker():
        # Load any persisted cache first
        _load_cache()
        with app.app_context():
            from app import db
            from app.models import Topic

            while not _stop_event.is_set():
                try:
                    # Get list of known topics from DB. If none, use default topic.
                    topics = [t.name for t in db.session.execute(db.select(Topic)).scalars().all()]
                    if not topics:
                        topics = ['Basic Grammar']

                    for topic in topics:
                        key = _pool_key(topic, "random")
                        with _lock:
                            q = _pools[key]
                            need = max(0, MIN_POOL - len(q))

                        if need > 0:
                            # Fetch up to PREFETCH_BATCH or until MAX_POOL
                            to_fetch = min(PREFETCH_BATCH, MAX_POOL - len(q))
                            for _ in range(to_fetch):
                                # Call generation inside app context
                                try:
                                    ex = _safe_generate(topic, mode="random")
                                except Exception:
                                    ex = None
                                if ex and ex.get('questions') and ex.get('source') != 'fallback':
                                    with _lock:
                                        _pools[key].append(ex)
                                else:
                                    # Do not cache fallback exercises; retry later for a real one.
                                    break

                    # Persist cache periodically
                    _save_cache()

                except Exception:
                    # swallow worker exceptions to keep thread alive
                    pass

                _stop_event.wait(SLEEP_INTERVAL)

    _thread = threading.Thread(target=worker, daemon=True, name='exercise-prefetcher')
    _thread.start()


def stop_prefetcher():
    global _stop_event, _thread
    if _stop_event:
        _stop_event.set()
    if _thread:
        _thread.join(timeout=1)


def get_exercise(
    topic: str,
    timeout: int = 5,
    level: str = "Intermediate",
    exercise_type: str = "Multiple Choice",
    streak_days: int | None = None,
    mode: str = "random",
    topic_type: str | None = None,
    subtopic_focus: str | None = None,
):
    """Get an exercise from pool if available, otherwise fall back to direct generation.

    Returns exercise dict or None on failure.
    """
    key = _pool_key(topic, mode, subtopic_focus)
    # Try get from pool quickly
    with _lock:
        if key in _pools and _pools[key]:
            try:
                cached = _pools[key].popleft()
                if isinstance(cached, dict) and cached.get('source') == 'fallback':
                    cached = None
                if cached:
                    return cached
            except Exception:
                pass
    # No cached item: generate synchronously (caller should be under app context)
    ex = _safe_generate(
        topic,
        level=level,
        exercise_type=exercise_type,
        streak_days=streak_days,
        mode=mode,
        topic_type=topic_type,
        subtopic_focus=subtopic_focus,
    )
    if not isinstance(ex, dict) or not ex.get('questions'):
        try:
            from app.llm_service import fallback_exercise
            return fallback_exercise(topic)
        except Exception:
            return None
    # After returning, trigger background fill by ensuring prefetcher will see lower pool
    return ex
