import os
import requests
import time
import json
from flask import current_app
from requests.exceptions import RequestException

# When AI service has repeated failures, skip network attempts for this cooldown
_COOLDOWN_SECONDS = int(os.environ.get('AI_SERVICE_COOLDOWN', '60'))
_last_failure = 0.0

# Force using real AI service even during cooldown (set env FORCE_REAL_AI=true)
_FORCE_REAL_AI = os.environ.get('FORCE_REAL_AI', 'false').lower() == 'true'
# Number of retries for transient network errors
_AI_RETRIES = int(os.environ.get('AI_SERVICE_RETRIES', '3'))
_AI_BACKOFF = float(os.environ.get('AI_SERVICE_BACKOFF', '1'))


def _call_ai_service(payload: dict, fallback_factory, log_label: str):
  global _last_failure

  api_url = os.environ.get('AI_SERVICE_URL', 'http://ai_service:5001/generate')
  secret_key = current_app.config.get('SECRET_KEY', '')

  headers = {
    'X-API-KEY': secret_key,
    'Content-Type': 'application/json',
    'Host': 'localhost'
  }

  current_app.logger.warning(
    "[llm_service] Requesting ai_service url=%s payload=%s",
    api_url,
    json.dumps(payload, ensure_ascii=False),
  )

  now = time.time()
  if _last_failure and (now - _last_failure) < _COOLDOWN_SECONDS and not _FORCE_REAL_AI:
    current_app.logger.warning("[llm_service] Cooldown active, using fallback for %s", log_label)
    return fallback_factory()

  attempt = 0
  while attempt < max(1, _AI_RETRIES):
    attempt += 1
    try:
      resp = requests.post(api_url, json=payload, headers=headers, timeout=10)
      preview = resp.text[:500].replace('\n', ' ')
      current_app.logger.warning(
        "[llm_service] ai_service response status=%s body=%s",
        resp.status_code,
        preview,
      )
      resp.raise_for_status()
      _last_failure = 0.0
      data = resp.json()
      return data
    except RequestException as e:
      errstr = str(e)
      details = ''
      status_code = None
      try:
        resp = getattr(e, 'response', None)
        if resp is not None:
          status_code = resp.status_code
          details = f" status={resp.status_code} body={resp.text!r}"
      except Exception:
        details = ''
      current_app.logger.warning(
        "[llm_service] Attempt %s/%s failed: %s.%s",
        attempt,
        _AI_RETRIES,
        errstr,
        details,
      )
      if status_code == 429:
        _last_failure = time.time()
        current_app.logger.warning(
          "[llm_service] Received 429 from ai_service. Skipping retries and using cooldown %ss.",
          _COOLDOWN_SECONDS,
        )
        break
      if attempt >= _AI_RETRIES:
        _last_failure = time.time()
        current_app.logger.warning(
          "[llm_service] All retries failed. Falling back. Cooldown %ss.",
          _COOLDOWN_SECONDS,
        )
        break
      time.sleep(_AI_BACKOFF * attempt)
    except Exception as e:
      _last_failure = time.time()
      current_app.logger.warning("[llm_service] Unexpected error: %s. Falling back.", e)
      break

  return fallback_factory()


def generate_exercise(
  topic: str,
  level: str = "Intermediate",
  exercise_type: str = "Multiple Choice",
  streak_days: int | None = None,
  mode: str = "random",
  topic_type: str | None = None,
  subtopic_focus: str | None = None,
) -> dict:
    """
    Calls the separated AI microservice to generate English exercises.
    Uses SECRET_KEY header and optional retries/backoff. Honors FORCE_REAL_AI to prefer network calls.
    Returns a dictionary parsed from JSON, or fallback on error.
    """
    payload = {
        "topic": topic,
      "request_type": "exercise_generation",
        "level": level,
        "exercise_type": exercise_type,
        "mode": mode,
    }
    if topic_type:
      payload["topic_type"] = topic_type
    if subtopic_focus:
      payload["subtopic_focus"] = subtopic_focus
    if streak_days is not None:
      payload["streak_days"] = int(streak_days)
    data = _call_ai_service(payload, lambda: fallback_exercise(topic), f"topic={topic}")
    if not isinstance(data, dict) or not data.get('questions'):
      current_app.logger.warning("[llm_service] Invalid exercise payload returned, using fallback for topic=%r", topic)
      data = fallback_exercise(topic)
    source = data.get('source', 'ai_service') if isinstance(data, dict) else 'ai_service'
    question_count = len(data.get('questions', [])) if isinstance(data, dict) else 0
    current_app.logger.warning(
      "[llm_service] Parsed response source=%s questions=%s",
      source,
      question_count,
    )
    return data


def generate_topic_suggestions(count: int = 10) -> list[dict]:
    """Ask AI to propose a topic catalog for the learning app."""

    payload = {
      "request_type": "topic_generation",
      "topic_count": int(count),
    }

    def _fallback_topics() -> dict:
      return {
        "source": "fallback",
        "topics": [
          {"name": "Conditionals", "description": "Type 0, 1, 2, and 3 conditionals", "topic_type": "grammar"},
          {"name": "Passive Voice", "description": "Passive structures across tenses", "topic_type": "grammar"},
          {"name": "Daily Vocabulary", "description": "High-frequency words for daily communication", "topic_type": "vocabulary"},
        ]
      }

    data = _call_ai_service(payload, _fallback_topics, "topic catalog")
    topics = data.get("topics", []) if isinstance(data, dict) else []
    normalized_topics: list[dict] = []
    for item in topics:
      if not isinstance(item, dict):
        continue
      name = (item.get("name") or "").strip()
      if not name:
        continue
      normalized_topics.append({
        "name": name,
        "description": (item.get("description") or "").strip(),
        "topic_type": ((item.get("topic_type") or "grammar").strip().lower() if isinstance(item.get("topic_type"), str) else "grammar"),
      })
    return normalized_topics


def generate_roadmap_suggestions() -> dict:
    """Ask AI to propose grammar and vocabulary roadmaps for dashboard display."""

    payload = {
      "request_type": "roadmap_generation",
    }

    def _fallback_roadmap() -> dict:
      return {
        "source": "fallback",
        "grammar_roadmap": [
          {"name": "Conditionals", "desc": "Cau dieu kien loai 0-3"},
          {"name": "Passive Voice", "desc": "Cau bi dong theo thi"},
          {"name": "Relative Clauses", "desc": "Menh de quan he"},
          {"name": "Reported Speech", "desc": "Cau tuong thuat"},
          {"name": "Gerunds and Infinitives", "desc": "V-ing va to V"},
          {"name": "Modal Verbs", "desc": "Can, could, should, must"},
        ],
        "vocabulary_roadmap": [
          {"name": "Daily Communication", "desc": "Giao tiep hang ngay"},
          {"name": "Travel and Transport", "desc": "Du lich va di chuyen"},
          {"name": "Food and Restaurant", "desc": "Am thuc va nha hang"},
          {"name": "Workplace English", "desc": "Tieng Anh cong viec"},
          {"name": "Technology", "desc": "Cong nghe va internet"},
          {"name": "Health and Lifestyle", "desc": "Suc khoe va loi song"},
        ],
      }

    data = _call_ai_service(payload, _fallback_roadmap, "roadmap")
    if not isinstance(data, dict):
      data = _fallback_roadmap()

    grammar_roadmap = data.get("grammar_roadmap", [])
    vocabulary_roadmap = data.get("vocabulary_roadmap", [])

    def _normalize(items):
      normalized = []
      for item in items:
        if not isinstance(item, dict):
          continue
        name = (item.get("name") or "").strip()
        if not name:
          continue
        desc = (item.get("desc") or item.get("description") or "").strip()
        normalized.append({"name": name, "desc": desc or "Chu de hoc tap"})
      return normalized

    grammar_norm = _normalize(grammar_roadmap)
    vocab_norm = _normalize(vocabulary_roadmap)
    if not grammar_norm or not vocab_norm:
      fallback = _fallback_roadmap()
      grammar_norm = grammar_norm or fallback["grammar_roadmap"]
      vocab_norm = vocab_norm or fallback["vocabulary_roadmap"]

    return {
      "grammar_roadmap": grammar_norm,
      "vocabulary_roadmap": vocab_norm,
    }


def fallback_exercise(topic: str) -> dict:
    """Return a local embedded exercise when the AI service is unavailable."""
    current_app.logger.warning("[llm_service] Returning embedded fallback for topic=%r", topic)
    return {
  "source": "fallback",
  "mode": "random",
  "questions": [
    {
      "question_type": "grammar",
      "subtopic": "Present Perfect",
      "answer": "have eaten",
      "explanation": "The phrase 'already' indicates an action completed before now, which is a key use of the Present Perfect tense. The correct form is 'have' + past participle ('eaten').",
      "options": [
        "eat",
        "ate",
        "have eaten",
        "am eating"
      ],
      "question": "I ______ my breakfast already."
    },
    {
      "question_type": "grammar",
      "subtopic": "Present Perfect",
      "answer": "Have / gone",
      "explanation": "To ask about past experiences up to the present moment, we use the Present Perfect tense with 'ever'. The structure is 'Have' + subject + 'ever' + past participle ('gone').",
      "options": [
        "Did / go",
        "Have / gone",
        "Are / going",
        "Do / go"
      ],
      "question": "______ you ever ______ to Paris?"
    },
    {
      "question_type": "grammar",
      "subtopic": "Present Perfect",
      "answer": "has / bought",
      "explanation": "'Just' indicates an action that happened a very short time ago, which requires the Present Perfect tense. For 'she', we use 'has' + past participle ('bought').",
      "options": [
        "has / bought",
        "is / buying",
        "buys / already",
        "did / buy"
      ],
      "question": "She ______ just ______ her new car."
    },
    {
      "question_type": "grammar",
      "subtopic": "Present Perfect",
      "answer": "have / for",
      "explanation": "To express a duration of time from the past until now, we use the Present Perfect tense with 'for' + a period of time (e.g., 'ten years'). 'Since' is used with a specific starting point.",
      "options": [
        "have / since",
        "have / for",
        "are / for",
        "did / since"
      ],
      "question": "We ______ lived in this city ______ ten years."
    },
    {
      "question_type": "grammar",
      "subtopic": "Present Perfect",
      "answer": "has lost",
      "explanation": "The action of losing the keys happened in the past, but the result (he can't open the door) is relevant now. This is a common use of the Present Perfect tense.",
      "options": [
        "lost",
        "has lost",
        "is losing",
        "loses"
      ],
      "question": "He ______ his keys, so he can't open the door."
    }
  ]
}
