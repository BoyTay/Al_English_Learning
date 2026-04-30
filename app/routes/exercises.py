from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from app import db
from app.models import Topic, UserTopicProgress, ExerciseHistory, UserSubtopicProgress
from app.queue_service import get_exercise as generate_exercise

from app.sm2 import map_score_to_quality, calculate_sm2
from datetime import datetime, timedelta, timezone

exercises_bp = Blueprint('exercises', __name__)
VALID_MODES = {"random"}


def _allowed_types_for_mode(mode: str) -> set[str]:
    return {"grammar", "vocabulary"}


def _infer_weak_skill_type(user_id: int) -> str | None:
    rows = db.session.execute(
        db.select(UserSubtopicProgress).where(UserSubtopicProgress.user_id == user_id)
    ).scalars().all()
    if not rows:
        return None

    totals = {
        "grammar": {"attempted": 0, "errors": 0},
        "vocabulary": {"attempted": 0, "errors": 0},
    }
    for row in rows:
        q_type = (row.question_type or "grammar").strip().lower()
        if q_type not in totals:
            continue
        attempted = int(row.total_attempted or 0)
        correct = int(row.correct_count or 0)
        totals[q_type]["attempted"] += attempted
        totals[q_type]["errors"] += max(0, attempted - correct)

    grammar_attempted = totals["grammar"]["attempted"]
    vocab_attempted = totals["vocabulary"]["attempted"]
    if grammar_attempted == 0 and vocab_attempted == 0:
        return None

    grammar_rate = totals["grammar"]["errors"] / max(1, grammar_attempted)
    vocab_rate = totals["vocabulary"]["errors"] / max(1, vocab_attempted)
    return "vocabulary" if vocab_rate > grammar_rate else "grammar"


def _score_topic_for_review(
    topic: Topic,
    progress: UserTopicProgress | None,
    now: datetime,
    error_threshold: float,
    weak_skill_type: str | None,
) -> tuple[float, float, float, float]:
    """Return a sortable score tuple: overdue priority, weakness, novelty."""
    if progress:
        threshold_boost = 1.0 if (progress.error_rate or 0.0) >= error_threshold else 0.0
        needs_review = (0.0 if progress.consecutive_high_scores >= 2 else 1.0) + threshold_boost
        if progress.next_review_date:
            overdue_days = max(0.0, (now.date() - progress.next_review_date.date()).days)
        else:
            overdue_days = 0.0
        weakness = float(progress.error_rate or 0.0)
        novelty = 0.0
    else:
        needs_review = 1.0
        overdue_days = 0.0
        weakness = 0.0
        novelty = 1.0
    skill_bonus = 1.0 if weak_skill_type and (topic.topic_type or "grammar") == weak_skill_type else 0.0
    return needs_review, overdue_days, weakness, novelty + skill_bonus


def _pick_next_topic(user_id: int, mode: str) -> tuple[Topic | None, UserTopicProgress | None]:
    now = datetime.now(timezone.utc)
    allowed_types = _allowed_types_for_mode(mode)
    topics = db.session.execute(db.select(Topic)).scalars().all()
    topics = [t for t in topics if (t.topic_type or "grammar") in allowed_types]
    if not topics:
        return None, None

    weak_skill_type = _infer_weak_skill_type(user_id)
    if weak_skill_type and weak_skill_type not in allowed_types:
        weak_skill_type = None

    threshold_value = float(current_user.error_threshold or 0.4)

    progress_map = {
        progress.topic_id: progress
        for progress in db.session.execute(
            db.select(UserTopicProgress).where(UserTopicProgress.user_id == user_id)
        ).scalars().all()
    }

    ranked = []
    for topic in topics:
        progress = progress_map.get(topic.id)
        needs_review, overdue_days, weakness, novelty = _score_topic_for_review(
            topic,
            progress,
            now,
            threshold_value,
            weak_skill_type,
        )
        ranked.append((needs_review, overdue_days, weakness, novelty, topic, progress))

    ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    _, _, _, _, topic, progress = ranked[0]
    return topic, progress

@exercises_bp.route('/new', methods=['GET'])
@login_required
def new_exercise():
    # Keep streak persisted so downstream flows always read fresh value.
    current_user.update_streak()
    db.session.commit()

    mode = (request.args.get('mode') or 'random').strip().lower()
    if mode not in VALID_MODES:
        mode = 'random'

    subtopic_focus = (request.args.get('subtopic') or '').strip()
    focus_type = (request.args.get('focus') or '').strip().lower()

    # If user chooses a topic explicitly, respect it.
    topic_name = "Basic Grammar"
    topic_type = "grammar"
    topic_id = None

    if focus_type in {'grammar', 'vocabulary'}:
        topic_type = focus_type

    if subtopic_focus:
        topic_name = subtopic_focus
        existing_focus_topic = db.session.scalar(
            db.select(Topic).where(
                Topic.name == subtopic_focus,
                Topic.topic_type == topic_type,
            )
        )
        if existing_focus_topic:
            topic_id = existing_focus_topic.id

    if request.args.get('topic_id'):
        topic = db.session.get(Topic, int(request.args.get('topic_id')))
        if topic:
            if (topic.topic_type or 'grammar') not in _allowed_types_for_mode(mode):
                flash('Chủ đề này không phù hợp với mode đang chọn. Hệ thống chuyển sang chủ đề phù hợp.', 'warning')
            else:
                topic_name = topic.name
                topic_id = topic.id
                topic_type = topic.topic_type or 'grammar'
    if not topic_id and not subtopic_focus:
        topic, progress = _pick_next_topic(current_user.id, mode)
        if topic:
            topic_name = topic.name
            topic_id = topic.id
            topic_type = topic.topic_type or 'grammar'
            if progress and progress.error_rate > 0.0:
                current_app.logger.warning(
                    "[exercises.new_exercise] picked topic=%r overdue=%s error_rate=%.3f next_review=%s",
                    topic_name,
                    max(0, (datetime.now(timezone.utc).date() - progress.next_review_date.date()).days) if progress.next_review_date else 0,
                    progress.error_rate,
                    progress.next_review_date,
                )
            
    # Gọi AI sinh bài tập
    exercise_data = generate_exercise(
        topic_name,
        streak_days=current_user.streak_days,
        mode=mode,
        topic_type=topic_type,
        subtopic_focus=subtopic_focus or None,
    )
    source = exercise_data.get('source', 'unknown') if isinstance(exercise_data, dict) else 'invalid'
    question_count = len(exercise_data.get('questions', [])) if isinstance(exercise_data, dict) else 0
    current_app.logger.warning(
        "[exercises.new_exercise] topic=%r type=%s mode=%s subtopic=%r source=%s questions=%s",
        topic_name,
        topic_type,
        mode,
        subtopic_focus,
        source,
        question_count,
    )

    if isinstance(exercise_data, dict):
        exercise_data['mode'] = mode
    
    return render_template(
        'exercise.html',
        exercise_data=exercise_data,
        topic_name=topic_name,
        topic_id=topic_id,
        topic_type=topic_type,
        mode=mode,
        subtopic_focus=subtopic_focus,
    )

@exercises_bp.route('/submit', methods=['POST'])
@login_required
def submit_exercise():
    data = request.json
    score = data.get('score', 0)
    total = data.get('total', 5)
    topic_name = data.get('topic_name')
    topic_id = data.get('topic_id')
    exercise_data = data.get('exercise_data')
    user_answers = data.get('user_answers') or []
    
    # Đảm bảo có topic trong CSDL
    if not topic_id and topic_name:
        topic = db.session.scalar(db.select(Topic).where(Topic.name == topic_name))
        if not topic:
            topic = Topic(name=topic_name, topic_type='grammar')
            db.session.add(topic)
            db.session.commit()
        topic_id = topic.id

    topic = db.session.get(Topic, int(topic_id)) if topic_id else None
        
    # Ghi nhận lịch sử làm bài   
    history = ExerciseHistory(
        user_id=current_user.id,
        topic_id=topic_id,
        exercise_data=exercise_data,
        score=score,
        total_questions=total
    )
    db.session.add(history)
    
    # SM-2 & Error Rate Tracker
    progress = db.session.scalar(
        db.select(UserTopicProgress).where(
            UserTopicProgress.user_id == current_user.id,
            UserTopicProgress.topic_id == topic_id
        )
    )
    if not progress:
        progress = UserTopicProgress(user_id=current_user.id, topic_id=topic_id)
        progress.total_attempted = 0
        progress.correct_count = 0
        progress.error_rate = 1.0
        progress.consecutive_high_scores = 0
        progress.easiness_factor = 2.5
        progress.interval = 0
        progress.repetitions = 0
        db.session.add(progress)
    
    # Calculate error rate mới
    progress.total_attempted += total
    progress.correct_count += score
    progress.error_rate = (progress.total_attempted - progress.correct_count) / max(1, progress.total_attempted)
    passed_this_round = total > 0 and score >= 4 and (score / total) >= 0.8
    if passed_this_round:
        progress.consecutive_high_scores = (progress.consecutive_high_scores or 0) + 1
    else:
        progress.consecutive_high_scores = 0
    
    # Cập nhật SM-2 properties
    quality = map_score_to_quality(score, total)
    new_rep, new_ef, new_interval = calculate_sm2(
        quality, progress.repetitions, progress.easiness_factor, progress.interval
    )
    
    progress.repetitions = new_rep
    progress.easiness_factor = new_ef
    progress.interval = new_interval
    progress.next_review_date = datetime.now(timezone.utc) + timedelta(days=new_interval)

    # Track weak areas at question/subtopic level.
    questions = exercise_data.get('questions', []) if isinstance(exercise_data, dict) else []
    for idx, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        correct_answer = (question.get('answer') or '').strip()
        selected_answer = ''
        if idx < len(user_answers):
            selected_answer = str(user_answers[idx] or '').strip()
        is_correct = bool(selected_answer) and selected_answer == correct_answer

        question_type = (question.get('question_type') or (topic.topic_type if topic else 'grammar') or 'grammar').strip().lower()
        if question_type not in {'grammar', 'vocabulary'}:
            question_type = 'grammar'
        subtopic = (question.get('subtopic') or topic_name or 'General').strip()

        sub_progress = db.session.scalar(
            db.select(UserSubtopicProgress).where(
                UserSubtopicProgress.user_id == current_user.id,
                UserSubtopicProgress.topic_id == topic_id,
                UserSubtopicProgress.question_type == question_type,
                UserSubtopicProgress.subtopic == subtopic,
            )
        )
        if not sub_progress:
            sub_progress = UserSubtopicProgress(
                user_id=current_user.id,
                topic_id=topic_id,
                question_type=question_type,
                subtopic=subtopic,
                total_attempted=0,
                correct_count=0,
                error_rate=1.0,
            )
            db.session.add(sub_progress)

        sub_progress.total_attempted += 1
        if is_correct:
            sub_progress.correct_count += 1
        sub_progress.error_rate = (sub_progress.total_attempted - sub_progress.correct_count) / max(1, sub_progress.total_attempted)
        sub_progress.updated_at = datetime.now(timezone.utc)
    
    db.session.commit()

    threshold = float(current_user.error_threshold or 0.4)
    threshold_exceeded = (progress.error_rate or 0.0) >= threshold
    
    return jsonify({
        "success": True, 
        "message": f"Bạn đạt {score}/{total} điểm!",
        "new_error_rate": round(progress.error_rate, 2),
        "next_review": progress.next_review_date.strftime("%Y-%m-%d"),
        "threshold": threshold,
        "threshold_exceeded": threshold_exceeded,
    })
