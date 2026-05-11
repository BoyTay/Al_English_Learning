import os
import requests
import time
import json
import re
import math
import random
import hashlib
from urllib.parse import quote
import threading
from collections import defaultdict, deque
from flask import current_app
from requests.exceptions import RequestException

# When AI service has repeated failures, skip network attempts for this cooldown
_COOLDOWN_SECONDS = int(os.environ.get('AI_SERVICE_COOLDOWN', '60'))
_MAX_QUOTA_COOLDOWN_SECONDS = int(os.environ.get('AI_SERVICE_MAX_QUOTA_COOLDOWN', '300'))
_last_failure = 0.0
_quota_cooldown_until = 0.0

# Force using real AI service even during cooldown (set env FORCE_REAL_AI=true)
_FORCE_REAL_AI = os.environ.get('FORCE_REAL_AI', 'false').lower() == 'true'
# Number of retries for transient network errors
_AI_RETRIES = int(os.environ.get('AI_SERVICE_RETRIES', '3'))
_AI_BACKOFF = float(os.environ.get('AI_SERVICE_BACKOFF', '1'))
_AI_TIMEOUT = int(os.environ.get('AI_SERVICE_TIMEOUT', '20'))

_DEFAULT_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get('AI_RATE_LIMIT_WINDOW_SECONDS', '60'))
_DEFAULT_RATE_LIMIT_MAX_REQUESTS = int(os.environ.get('AI_RATE_LIMIT_MAX_REQUESTS', '10'))
_rate_limit_state: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = threading.Lock()

_response_cache: dict[str, tuple[float, dict | list]] = {}
_response_cache_lock = threading.Lock()


def _extract_retry_delay_seconds(text: str) -> int | None:
  if not text:
    return None
  patterns = [
    r"retryDelay\\?['\"\\s:]*\\?['\"]?([0-9]+(?:\.[0-9]+)?)s['\"]?",
    r"please\s+retry\s+in\s+([0-9]+(?:\.[0-9]+)?)s",
  ]
  for pattern in patterns:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
      continue
    try:
      return max(1, int(math.ceil(float(match.group(1)))))
    except (TypeError, ValueError):
      continue
  return None


def _deep_clone_json_data(value):
  try:
    return json.loads(json.dumps(value, ensure_ascii=False))
  except Exception:
    return value


def get_image_for_query(query: str, exact_term: str = None) -> str:
    cleaned = (query or "").strip()
    term_to_search = (exact_term or cleaned).strip()
    if not term_to_search:
        return ""
    
    # Try Pixabay if API key exists
    pixabay_key = os.environ.get('PIXABAY_API_KEY')
    if pixabay_key:
        try:
            # Pixabay works best with short keywords (the exact term)
            import re
            search_query = re.sub(r'[^a-zA-Z0-9\s]', '', term_to_search)
            url = f"https://pixabay.com/api/?key={pixabay_key}&q={quote(search_query)}&image_type=photo&orientation=horizontal&per_page=3"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('hits') and len(data['hits']) > 0:
                    return data['hits'][0]['webformatURL']
        except Exception as e:
            if current_app:
                current_app.logger.warning(f"[llm_service] Pixabay error: {e}")
            
    # Fallback to placeholder if no key or no results
    display_text = quote(" ".join(cleaned.split()[:3]))
    return f"https://placehold.co/512x384/2c3e50/ffffff?text={display_text}"


def check_rate_limit(
  *,
  actor_key: str,
  bucket: str,
  max_requests: int | None = None,
  window_seconds: int | None = None,
) -> tuple[bool, int]:
  max_r = int(max_requests or _DEFAULT_RATE_LIMIT_MAX_REQUESTS)
  window_s = int(window_seconds or _DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
  now = time.time()
  state_key = f"{bucket}::{actor_key}"
  with _rate_limit_lock:
    history = _rate_limit_state[state_key]
    while history and (now - history[0]) > window_s:
      history.popleft()
    if len(history) >= max_r:
      retry_after = max(1, int(math.ceil(window_s - (now - history[0]))))
      return False, retry_after
    history.append(now)
  return True, 0


def _call_ai_service(
  payload: dict,
  fallback_factory,
  log_label: str,
  *,
  cache_ttl_seconds: int = 0,
  cache_key_suffix: str | None = None,
):
  global _last_failure, _quota_cooldown_until

  api_url = os.environ.get('AI_SERVICE_URL', 'http://ai_service:5001/generate')
  secret_key = current_app.config.get('SECRET_KEY', '')
  cache_key = None
  if cache_ttl_seconds > 0:
    normalized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    cache_key = f"{cache_key_suffix or log_label}::{normalized_payload}"
    now = time.time()
    with _response_cache_lock:
      cached = _response_cache.get(cache_key)
      if cached:
        expires_at, cached_data = cached
        if now < expires_at:
          current_app.logger.warning("[llm_service] Cache hit for %s", log_label)
          return _deep_clone_json_data(cached_data)
        _response_cache.pop(cache_key, None)

  headers = {
    'X-API-KEY': secret_key,
    'Content-Type': 'application/json',
    'Host': 'localhost'
  }

  now = time.time()
  if _quota_cooldown_until and now < _quota_cooldown_until and not _FORCE_REAL_AI:
    current_app.logger.warning(
      "[llm_service] Quota cooldown active (%ss left), using fallback for %s",
      int(_quota_cooldown_until - now),
      log_label,
    )
    return fallback_factory()
  if _last_failure and (now - _last_failure) < _COOLDOWN_SECONDS and not _FORCE_REAL_AI:
    current_app.logger.warning("[llm_service] Cooldown active, using fallback for %s", log_label)
    return fallback_factory()

  current_app.logger.warning(
    "[llm_service] Requesting ai_service url=%s payload=%s",
    api_url,
    json.dumps(payload, ensure_ascii=False),
  )

  attempt = 0
  while attempt < max(1, _AI_RETRIES):
    attempt += 1
    try:
      resp = requests.post(api_url, json=payload, headers=headers, timeout=_AI_TIMEOUT)
      preview = resp.text[:500].replace('\n', ' ')
      current_app.logger.warning(
        "[llm_service] ai_service response status=%s body=%s",
        resp.status_code,
        preview,
      )
      resp.raise_for_status()
      _last_failure = 0.0
      _quota_cooldown_until = 0.0
      data = resp.json()
      if cache_key and cache_ttl_seconds > 0:
        with _response_cache_lock:
          _response_cache[cache_key] = (time.time() + cache_ttl_seconds, _deep_clone_json_data(data))
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
      if status_code in {429, 503}:
        details_lower = details.lower()
        if status_code == 429:
          retry_seconds = _extract_retry_delay_seconds(details) or _extract_retry_delay_seconds(errstr)
          quota_cooldown = _COOLDOWN_SECONDS
          if retry_seconds is not None:
            quota_cooldown = min(
              _MAX_QUOTA_COOLDOWN_SECONDS,
              max(5, retry_seconds + 2),
            )
          elif "generaterequestsperday" in details_lower or (
            "quota exceeded for metric" in details_lower and "perday" in details_lower
          ):
            quota_cooldown = _MAX_QUOTA_COOLDOWN_SECONDS
          _quota_cooldown_until = time.time() + quota_cooldown
          _last_failure = 0.0
          current_app.logger.warning(
            "[llm_service] 429 quota detected. Applying %ss cooldown.",
            int(quota_cooldown),
          )
          current_app.logger.warning(
            "[llm_service] Received 429 from ai_service. Skipping retries and using quota cooldown."
          )
        else:
          _last_failure = time.time()
          current_app.logger.warning(
            "[llm_service] Received %s from ai_service. Skipping retries and using cooldown %ss.",
            status_code,
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

    data = _call_ai_service(
      payload,
      _fallback_topics,
      "topic catalog",
      cache_ttl_seconds=3600,
      cache_key_suffix="topic-catalog",
    )
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

    data = _call_ai_service(
      payload,
      _fallback_roadmap,
      "roadmap",
      cache_ttl_seconds=900,
      cache_key_suffix="roadmap",
    )
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


def generate_daily_writing_prompt(date_key: str) -> dict:
    """Ask AI to generate a writing prompt of the day."""
    payload = {
      "request_type": "writing_prompt_generation",
      "date_key": date_key,
    }

    def _fallback_prompt() -> dict:
      return {
        "source": "fallback",
        "topic": "A lesson I learned this week",
        "instructions": "Write 120-180 words. Describe the situation, what you learned, and how you will apply it.",
        "sample_outline": [
          "Opening: context and situation",
          "Body: what happened and your reaction",
          "Conclusion: specific lesson and next action",
        ],
      }

    data = _call_ai_service(
      payload,
      _fallback_prompt,
      "writing prompt",
      cache_ttl_seconds=21600,
      cache_key_suffix=f"writing-prompt::{date_key}",
    )
    if not isinstance(data, dict):
      data = _fallback_prompt()

    topic = (data.get("topic") or "").strip()
    instructions = (data.get("instructions") or "").strip()
    sample_outline = data.get("sample_outline") if isinstance(data.get("sample_outline"), list) else []
    sample_outline = [str(item).strip() for item in sample_outline if str(item).strip()]
    if not topic or not instructions:
      fallback = _fallback_prompt()
      topic = topic or fallback["topic"]
      instructions = instructions or fallback["instructions"]
      if not sample_outline:
        sample_outline = fallback["sample_outline"]

    return {
      "topic": topic,
      "instructions": instructions,
      "sample_outline": sample_outline,
    }


def evaluate_writing_submission(topic: str, instructions: str, user_text: str) -> dict:
    """Ask AI to evaluate a user's writing and return structured feedback."""
    payload = {
      "request_type": "writing_evaluation",
      "topic": topic,
      "instructions": instructions,
      "user_text": user_text,
    }

    def _fallback_evaluation() -> dict:
      return {
        "source": "fallback",
        "score": 6.0,
        "feedback_summary": "Bai viet co y tuong ro, nhung can cai thien lien ket cau va chinh xac ngu phap.",
        "corrected_text": user_text,
        "strengths": ["Co cau truc doan van", "Bam sat chu de"],
        "improvement_points": [
          "Dung da dang lien tu hon",
          "Kiem tra lai thi dong tu va danh-tu so it/so nhieu",
        ],
        "error_tags": ["tense", "articles", "word_choice"],
      }

    data = _call_ai_service(payload, _fallback_evaluation, "writing evaluation")
    if not isinstance(data, dict):
      data = _fallback_evaluation()

    try:
      score = float(data.get("score", 0.0))
    except (TypeError, ValueError):
      score = 0.0
    score = max(0.0, min(10.0, score))

    feedback_summary = (data.get("feedback_summary") or "").strip()
    corrected_text = (data.get("corrected_text") or "").strip() or user_text
    strengths = data.get("strengths") if isinstance(data.get("strengths"), list) else []
    improvement_points = data.get("improvement_points") if isinstance(data.get("improvement_points"), list) else []
    error_tags = data.get("error_tags") if isinstance(data.get("error_tags"), list) else []

    strengths = [str(item).strip() for item in strengths if str(item).strip()]
    improvement_points = [str(item).strip() for item in improvement_points if str(item).strip()]
    error_tags = [str(item).strip().lower() for item in error_tags if str(item).strip()]

    if not feedback_summary:
      feedback_summary = _fallback_evaluation()["feedback_summary"]
    if not strengths:
      strengths = _fallback_evaluation()["strengths"]
    if not improvement_points:
      improvement_points = _fallback_evaluation()["improvement_points"]

    return {
      "score": score,
      "feedback_summary": feedback_summary,
      "corrected_text": corrected_text,
      "strengths": strengths,
      "improvement_points": improvement_points,
      "error_tags": error_tags,
    }


def generate_flashcards(topic: str, count: int = 10) -> list[dict]:
    """Ask AI to generate a vocabulary flashcard set."""
    payload = {
      "request_type": "flashcard_generation",
      "topic": topic,
      "card_count": int(count),
    }

    def _fallback_flashcards() -> dict:
      return {
        "source": "fallback",
        "cards": [
          {
            "term": "adapt",
            "definition": "to change your behavior to suit a new situation",
            "meaning_vi": "thích nghi",
            "part_of_speech": "verb",
            "example_sentence": "It took me a week to adapt to my new class schedule.",
            "pronunciation_hint": "uh-DAPT",
            "ipa_pronunciation": "/əˈdæpt/",
            "image_hint": "student adjusting to a new classroom",
            "image_url": "",
          },
          {
            "term": "deadline",
            "definition": "the latest time by which something must be finished",
            "meaning_vi": "hạn chót",
            "part_of_speech": "noun",
            "example_sentence": "Our essay deadline is next Friday.",
            "pronunciation_hint": "DED-line",
            "ipa_pronunciation": "/ˈded.laɪn/",
            "image_hint": "calendar with a marked due date",
            "image_url": "",
          },
          {
            "term": "confident",
            "definition": "feeling sure about your abilities",
            "meaning_vi": "tự tin",
            "part_of_speech": "adjective",
            "example_sentence": "She felt confident before her speaking test.",
            "pronunciation_hint": "KON-fi-dent",
            "ipa_pronunciation": "/ˈkɒn.fɪ.dənt/",
            "image_hint": "student speaking with confidence",
            "image_url": "",
          },
        ],
      }

    data = _call_ai_service(
      payload,
      _fallback_flashcards,
      "flashcard generation",
      cache_ttl_seconds=900,
      cache_key_suffix=f"flashcards::{topic.lower()}::{int(count)}",
    )
    cards = data.get("cards", []) if isinstance(data, dict) else []
    normalized_cards: list[dict] = []
    for item in cards:
      if not isinstance(item, dict):
        continue
      term = (item.get("term") or "").strip()
      definition = (item.get("definition") or "").strip()
      meaning_vi = (item.get("meaning_vi") or item.get("vietnamese_meaning") or "").strip()
      part_of_speech = (item.get("part_of_speech") or item.get("pos") or "").strip().lower()
      ipa_pronunciation = (item.get("ipa_pronunciation") or item.get("ipa") or "").strip()
      image_url = (item.get("image_url") or "").strip()
      if not term or not definition:
        continue
      if not image_url:
        image_url = get_image_for_query(item.get("image_hint") or term, exact_term=term)
      normalized_cards.append({
        "term": term,
        "definition": definition,
        "meaning_vi": meaning_vi,
        "part_of_speech": part_of_speech,
        "example_sentence": (item.get("example_sentence") or "").strip(),
        "pronunciation_hint": (item.get("pronunciation_hint") or "").strip(),
        "ipa_pronunciation": ipa_pronunciation,
        "image_hint": (item.get("image_hint") or "").strip(),
        "image_url": image_url,
      })

    if not normalized_cards:
      fallback = _fallback_flashcards()
      normalized_cards = fallback["cards"]

    for card in normalized_cards:
      if not isinstance(card, dict):
        continue
      if card.get("image_url"):
        continue
      prompt = (card.get("image_hint") or card.get("term") or "").strip()
      image_url = get_image_for_query(prompt, exact_term=card.get("term"))
      if image_url:
        card["image_url"] = image_url
    return normalized_cards


def generate_speaking_prompts(count: int = 6) -> list[dict]:
    """Ask AI to generate short speaking/listening practice prompts."""
    requested_count = max(3, min(24, int(count)))
    payload = {
      "request_type": "speaking_prompt_generation",
      "prompt_count": requested_count,
    }

    def _fallback_prompts() -> dict:
      return {
        "source": "fallback",
        "prompts": [
          {
            "topic": "Daily routine",
            "sentence": "I usually wake up at six thirty and review English for fifteen minutes.",
            "difficulty": "easy",
          },
          {
            "topic": "Travel",
            "sentence": "Could you tell me which platform the train to Da Nang departs from?",
            "difficulty": "medium",
          },
          {
            "topic": "Work meeting",
            "sentence": "Let's summarize the key points before we decide the next action items.",
            "difficulty": "medium",
          },
          {
            "topic": "Health",
            "sentence": "I have had a slight headache since yesterday, so I need to rest tonight.",
            "difficulty": "medium",
          },
          {
            "topic": "Shopping",
            "sentence": "Do you have this jacket in a larger size, preferably in dark blue?",
            "difficulty": "easy",
          },
          {
            "topic": "Opinion",
            "sentence": "In my opinion, learning by teaching others helps us remember information longer.",
            "difficulty": "hard",
          },
        ],
      }

    data = _call_ai_service(
      payload,
      _fallback_prompts,
      "speaking prompt generation",
      cache_ttl_seconds=120,
      cache_key_suffix=f"speaking-prompts::{requested_count}",
    )
    prompts = data.get("prompts", []) if isinstance(data, dict) else []
    normalized_prompts: list[dict] = []
    for item in prompts:
      if not isinstance(item, dict):
        continue
      sentence = (item.get("sentence") or "").strip()
      topic = (item.get("topic") or "").strip() or "General"
      difficulty = (item.get("difficulty") or "medium").strip().lower()
      if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"
      if not sentence:
        continue
      normalized_prompts.append({
        "topic": topic,
        "sentence": sentence,
        "difficulty": difficulty,
      })
    if not normalized_prompts:
      fallback_pool = _fallback_prompts()["prompts"][:]
      random.shuffle(fallback_pool)
      normalized_prompts = fallback_pool

    fallback_pool = _fallback_prompts()["prompts"]
    existing_sentences = {p.get("sentence", "") for p in normalized_prompts}
    for candidate in fallback_pool:
      if len(normalized_prompts) >= requested_count:
        break
      sentence = candidate.get("sentence", "")
      if sentence in existing_sentences:
        continue
      normalized_prompts.append(candidate)
      existing_sentences.add(sentence)

    while len(normalized_prompts) < requested_count and fallback_pool:
      normalized_prompts.append(fallback_pool[len(normalized_prompts) % len(fallback_pool)])

    return normalized_prompts[:requested_count]


def explain_wrong_answer(
  question: str,
  options: list[str],
  correct_answer: str,
  user_answer: str,
  topic: str = "General English",
  question_type: str = "grammar",
) -> dict:
    payload = {
      "request_type": "answer_explanation",
      "topic": topic,
      "question_type": question_type,
      "question": question,
      "options": options,
      "correct_answer": correct_answer,
      "user_answer": user_answer,
    }

    def _fallback() -> dict:
      return {
        "summary": "Ban chon dap an chua dung vi chua phu hop ngu canh cau.",
        "why_wrong": "Dap an ban chon khong dung voi thi/ngu nghia ma cau yeu cau.",
        "memory_tip": "Doc ky dau hieu thoi gian trong cau, sau do doi chieu cau truc ngu phap.",
      }

    data = _call_ai_service(
      payload,
      _fallback,
      "answer explanation",
      cache_ttl_seconds=600,
      cache_key_suffix="answer-explanation",
    )
    if not isinstance(data, dict):
      data = _fallback()
    return {
      "summary": (data.get("summary") or _fallback()["summary"]).strip(),
      "why_wrong": (data.get("why_wrong") or _fallback()["why_wrong"]).strip(),
      "memory_tip": (data.get("memory_tip") or _fallback()["memory_tip"]).strip(),
    }


def chat_with_tutor(message_history: list[dict], user_message: str) -> dict:
    payload = {
      "request_type": "tutor_chat",
      "message_history": message_history[-20:],
      "user_message": user_message,
    }

    def _fallback() -> dict:
      return {
        "assistant_reply": "Great try! Could you tell me more about your day? I can help correct your grammar while we chat.",
        "corrections": [],
        "error_tags": [],
      }

    data = _call_ai_service(payload, _fallback, "tutor chat")
    if not isinstance(data, dict):
      data = _fallback()
    corrections = data.get("corrections") if isinstance(data.get("corrections"), list) else []
    error_tags = data.get("error_tags") if isinstance(data.get("error_tags"), list) else []
    return {
      "assistant_reply": (data.get("assistant_reply") or _fallback()["assistant_reply"]).strip(),
      "corrections": [str(c).strip() for c in corrections if str(c).strip()],
      "error_tags": [str(t).strip().lower() for t in error_tags if str(t).strip()],
    }


def summarize_chat_session(message_history: list[dict]) -> dict:
    payload = {
      "request_type": "tutor_chat_summary",
      "message_history": message_history[-40:],
    }

    def _fallback() -> dict:
      return {
        "summary": "Nguoi hoc giao tiep tot, can tiep tuc luyen thi dong tu va dat cau tu nhien hon.",
        "key_errors": ["tense", "word_choice"],
      }

    data = _call_ai_service(
      payload,
      _fallback,
      "chat summary",
      cache_ttl_seconds=180,
      cache_key_suffix="chat-summary",
    )
    if not isinstance(data, dict):
      data = _fallback()
    key_errors = data.get("key_errors") if isinstance(data.get("key_errors"), list) else []
    return {
      "summary": (data.get("summary") or _fallback()["summary"]).strip(),
      "key_errors": [str(t).strip().lower() for t in key_errors if str(t).strip()],
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
