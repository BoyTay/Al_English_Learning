from datetime import datetime, timedelta, timezone

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.gamification import apply_flashcard_rewards
from app.llm_service import generate_flashcards, check_rate_limit
from app.models import Flashcard, FlashcardSet
from app.sm2 import calculate_sm2

flashcards_bp = Blueprint('flashcards', __name__, url_prefix='/flashcards')


def _as_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@flashcards_bp.route('/', methods=['GET'])
@login_required
def index():
    sets = db.session.execute(
        db.select(FlashcardSet)
        .where(FlashcardSet.user_id == current_user.id)
        .order_by(FlashcardSet.created_at.desc())
        .limit(12)
    ).scalars().all()

    now = datetime.utcnow()
    set_summaries = []
    for card_set in sets:
        cards = db.session.execute(
            db.select(Flashcard).where(Flashcard.set_id == card_set.id)
        ).scalars().all()
        due_count = sum(
            1
            for card in cards
            if (review_time := _as_utc_naive(card.next_review_date)) and review_time <= now
        )
        set_summaries.append({
            "set": card_set,
            "card_count": len(cards),
            "due_count": due_count,
        })

    return render_template('flashcards.html', set_summaries=set_summaries)


@flashcards_bp.route('/generate', methods=['POST'])
@login_required
def generate():
    topic = (request.form.get('topic') or '').strip()
    if not topic:
        flash('Vui lòng nhập chủ đề để sinh flashcard.', 'warning')
        return redirect(url_for('flashcards.index'))
    allowed, retry_after = check_rate_limit(
        actor_key=f"user:{current_user.id}",
        bucket='flashcard-generation',
        max_requests=4,
        window_seconds=60,
    )
    if not allowed:
        flash(f'Bạn tạo flashcard quá nhanh. Vui lòng thử lại sau {retry_after} giây.', 'warning')
        return redirect(url_for('flashcards.index'))

    card_count = request.form.get('card_count', type=int) or 10
    card_count = max(3, min(20, card_count))
    cards = generate_flashcards(topic, count=card_count)

    card_set = FlashcardSet(
        user_id=current_user.id,
        title=f"Flashcard: {topic}",
        source_topic=topic,
    )
    db.session.add(card_set)
    db.session.flush()

    now = datetime.utcnow()
    for item in cards:
        db.session.add(Flashcard(
            set_id=card_set.id,
            user_id=current_user.id,
            term=(item.get('term') or '').strip(),
            definition=(item.get('definition') or '').strip(),
            example_sentence=(item.get('example_sentence') or '').strip(),
            pronunciation_hint=(item.get('pronunciation_hint') or '').strip(),
            image_hint=(item.get('image_hint') or '').strip(),
            easiness_factor=2.5,
            interval=0,
            repetitions=0,
            next_review_date=now,
            review_count=0,
            created_at=now,
            updated_at=now,
        ))

    db.session.commit()
    flash(f'Đã sinh {len(cards)} flashcard cho chủ đề "{topic}".', 'success')
    return redirect(url_for('flashcards.review', set_id=card_set.id))


@flashcards_bp.route('/review', methods=['GET'])
@login_required
def review():
    set_id = request.args.get('set_id', type=int)
    now = datetime.utcnow()

    query = db.select(Flashcard).where(
        Flashcard.user_id == current_user.id,
        Flashcard.next_review_date <= now,
    )
    if set_id:
        query = query.where(Flashcard.set_id == set_id)

    due_cards = db.session.execute(
        query.order_by(Flashcard.next_review_date.asc()).limit(30)
    ).scalars().all()

    selected_set = None
    if set_id:
        selected_set = db.session.scalar(
            db.select(FlashcardSet).where(
                FlashcardSet.id == set_id,
                FlashcardSet.user_id == current_user.id,
            )
        )
    return render_template(
        'flashcard_review.html',
        due_cards=due_cards,
        selected_set=selected_set,
        set_id=set_id,
    )


@flashcards_bp.route('/review', methods=['POST'])
@login_required
def submit_review():
    card_id = request.form.get('card_id', type=int)
    set_id = request.form.get('set_id', type=int)
    quality = request.form.get('quality', type=int)
    quality = 0 if quality is None else max(0, min(5, quality))

    card = db.session.get(Flashcard, card_id) if card_id else None
    if not card or card.user_id != current_user.id:
        flash('Không tìm thấy flashcard để cập nhật.', 'danger')
        return redirect(url_for('flashcards.review', set_id=set_id))

    new_rep, new_ef, new_interval = calculate_sm2(
        quality=quality,
        repetitions=card.repetitions or 0,
        easiness_factor=card.easiness_factor or 2.5,
        interval=card.interval or 0,
    )
    now = datetime.utcnow()
    card.repetitions = new_rep
    card.easiness_factor = new_ef
    card.interval = new_interval
    card.last_quality = quality
    card.review_count = int(card.review_count or 0) + 1
    card.next_review_date = now + timedelta(days=new_interval)
    card.updated_at = now
    reward_summary = apply_flashcard_rewards(current_user, quality=quality)
    db.session.commit()
    if reward_summary["xp_gain"] > 0:
        flash(
            f"+{reward_summary['xp_gain']} XP từ ôn flashcard (Level {reward_summary['level']}).",
            "success"
        )

    return redirect(url_for('flashcards.review', set_id=set_id))
