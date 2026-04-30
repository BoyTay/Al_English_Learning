from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app import db
from app.models import Topic, UserTopicProgress, UserSubtopicProgress, UserRoadmapCache
from datetime import datetime, timezone


def _get_cached_or_generate_roadmap(user_id: int, force_refresh: bool = False) -> tuple[list[dict], list[dict], datetime]:
    now = datetime.now(timezone.utc)
    cache = db.session.scalar(
        db.select(UserRoadmapCache).where(UserRoadmapCache.user_id == user_id)
    )

    use_cache = False
    if cache and cache.updated_at and not force_refresh:
        use_cache = cache.updated_at.date() == now.date()

    if use_cache:
        grammar = cache.grammar_roadmap or []
        vocab = cache.vocabulary_roadmap or []
        if grammar and vocab:
            return grammar, vocab, cache.updated_at

    try:
        from app.llm_service import generate_roadmap_suggestions
        roadmap = generate_roadmap_suggestions()
    except Exception:
        roadmap = {
            "grammar_roadmap": [],
            "vocabulary_roadmap": [],
        }

    grammar = roadmap.get("grammar_roadmap", [])
    vocab = roadmap.get("vocabulary_roadmap", [])

    if not cache:
        cache = UserRoadmapCache(user_id=user_id)
        db.session.add(cache)
    cache.grammar_roadmap = grammar
    cache.vocabulary_roadmap = vocab
    cache.updated_at = now
    db.session.commit()
    return grammar, vocab, now

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('index.html')

@main_bp.route('/dashboard')
@login_required
def dashboard():
    # Update streak
    current_user.update_streak()
    db.session.commit()
    
    progress = current_user.progress
    subtopic_progress = current_user.subtopic_progress
    refresh_roadmap = (request.args.get('refresh_roadmap') == '1')
    history_filter = (request.args.get('history_filter') or 'all').strip().lower()
    if history_filter not in {'all', 'review'}:
        history_filter = 'all'
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        page = 1
    page_size = 5
    
    weakest_topic = None
    if progress:
        weakest_topic = max(progress, key=lambda x: x.error_rate).topic.name

    threshold = float(current_user.error_threshold or 0.4)
    now = datetime.now(timezone.utc)
    recommendation_cards = []
    for p in progress:
        overdue_days = 0
        if p.next_review_date:
            overdue_days = max(0, (now.date() - p.next_review_date.date()).days)
        threshold_exceeded = (p.error_rate or 0.0) >= threshold
        priority = (
            1 if threshold_exceeded else 0,
            float(p.error_rate or 0.0),
            overdue_days,
        )
        recommendation_cards.append({
            "topic_name": p.topic.name,
            "topic_id": p.topic_id,
            "topic_type": p.topic.topic_type,
            "error_rate": p.error_rate or 0.0,
            "overdue_days": overdue_days,
            "threshold_exceeded": threshold_exceeded,
            "priority": priority,
        })
    recommendation_cards.sort(key=lambda x: x["priority"], reverse=True)
    actionable_recommendations = [
        c for c in recommendation_cards
        if c["threshold_exceeded"] or c["overdue_days"] > 0 or c["error_rate"] >= 0.15
    ]
    if not actionable_recommendations and recommendation_cards:
        actionable_recommendations = recommendation_cards[:1]

    for c in actionable_recommendations:
        if c["threshold_exceeded"]:
            c["reason"] = "Vượt ngưỡng lỗi"
            c["reason_level"] = "high"
        elif c["overdue_days"] > 0:
            c["reason"] = f"Quá hạn {c['overdue_days']} ngày"
            c["reason_level"] = "medium"
        else:
            c["reason"] = "Cần cải thiện"
            c["reason_level"] = "low"

    recommendation_cards = actionable_recommendations[:3]
    primary_recommendation = recommendation_cards[0] if recommendation_cards else None

    grammar_stats = [s for s in subtopic_progress if (s.question_type or 'grammar') == 'grammar']
    vocab_stats = [s for s in subtopic_progress if (s.question_type or 'grammar') == 'vocabulary']
    grammar_error = sum((s.error_rate or 0.0) for s in grammar_stats) / max(1, len(grammar_stats))
    vocab_error = sum((s.error_rate or 0.0) for s in vocab_stats) / max(1, len(vocab_stats))
    weak_skill_type = None
    if grammar_stats or vocab_stats:
        weak_skill_type = 'vocabulary' if vocab_error > grammar_error else 'grammar'

    mode_suggestions = ['random']

    grammar_roadmap, vocabulary_roadmap, roadmap_updated_at = _get_cached_or_generate_roadmap(
        current_user.id,
        force_refresh=refresh_roadmap,
    )

    all_progress_sorted = sorted(
        progress,
        key=lambda p: (
            0 if p.next_review_date and p.next_review_date.date() <= now.date() else 1,
            -(p.error_rate or 0.0),
        )
    )

    weak_candidates = [p for p in progress if (p.total_attempted or 0) > 0]
    if not weak_candidates:
        weak_candidates = list(progress)
    weak_candidates.sort(key=lambda p: (p.error_rate or 0.0), reverse=True)
    weakness_analysis = [
        {
            "name": p.topic.name,
            "error_percent": round((p.error_rate or 0.0) * 100, 1),
        }
        for p in weak_candidates[:5]
    ] if progress else []

    def _needs_review(p: UserTopicProgress) -> bool:
        overdue = bool(p.next_review_date and p.next_review_date.date() <= now.date())
        return overdue or (p.error_rate or 0.0) >= threshold or (p.error_rate or 0.0) >= 0.15

    if history_filter == 'review':
        filtered_progress = [p for p in progress if (p.total_attempted or 0) > 0]
        if not filtered_progress:
            filtered_progress = list(progress)
        filtered_progress.sort(key=lambda p: (p.error_rate or 0.0), reverse=True)
    else:
        filtered_progress = all_progress_sorted

    total_items = len(filtered_progress)
    start = (page - 1) * page_size
    end = start + page_size
    history_rows = filtered_progress[start:end]
    has_more = end < total_items
    has_prev = page > 1

    if refresh_roadmap:
        flash('Đã làm mới lộ trình AI cho hôm nay.', 'success')
        
    return render_template('dashboard.html', 
                           weakest_topic=weakest_topic,
                           progress=history_rows,
                           threshold=threshold,
                           recommendation_cards=recommendation_cards,
                           primary_recommendation=primary_recommendation,
                           weak_skill_type=weak_skill_type,
                           mode_suggestions=mode_suggestions,
                           grammar_roadmap=grammar_roadmap,
                           vocabulary_roadmap=vocabulary_roadmap,
                           roadmap_updated_at=roadmap_updated_at,
                           weakness_analysis=weakness_analysis,
                           history_filter=history_filter,
                           history_page=page,
                           history_has_more=has_more,
                           history_has_prev=has_prev)


@main_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        raw_value = request.form.get('error_threshold', '0.4')
        try:
            parsed = float(raw_value)
        except ValueError:
            parsed = float(current_user.error_threshold or 0.4)
        parsed = max(0.05, min(0.95, parsed))
        current_user.error_threshold = parsed
        db.session.commit()
        flash('Đã cập nhật ngưỡng lỗi thành công.', 'success')
        return redirect(url_for('main.settings'))

    return render_template('settings.html', threshold=float(current_user.error_threshold or 0.4))
