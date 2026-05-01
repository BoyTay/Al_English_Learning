from collections import Counter
from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.gamification import apply_writing_rewards
from app.llm_service import evaluate_writing_submission, generate_daily_writing_prompt, check_rate_limit
from app.models import WritingPrompt, WritingSubmission

writing_bp = Blueprint('writing', __name__, url_prefix='/writing')


def _refresh_daily_prompt() -> WritingPrompt:
    today_key = datetime.now(timezone.utc).date().isoformat()
    prompt = db.session.scalar(
        db.select(WritingPrompt).where(WritingPrompt.prompt_date == today_key)
    )
    generated = generate_daily_writing_prompt(today_key)
    if prompt:
        prompt.topic = (generated.get('topic') or 'Daily writing').strip()
        prompt.instructions = (generated.get('instructions') or 'Write 120-180 words.').strip()
        prompt.sample_outline = generated.get('sample_outline') or []
        db.session.commit()
        return prompt

    prompt = WritingPrompt(
        prompt_date=today_key,
        topic=(generated.get('topic') or 'Daily writing').strip(),
        instructions=(generated.get('instructions') or 'Write 120-180 words.').strip(),
        sample_outline=generated.get('sample_outline') or [],
    )
    db.session.add(prompt)
    db.session.commit()
    return prompt


def _get_or_create_daily_prompt() -> WritingPrompt:
    today_key = datetime.now(timezone.utc).date().isoformat()
    prompt = db.session.scalar(
        db.select(WritingPrompt).where(WritingPrompt.prompt_date == today_key)
    )
    if prompt:
        return prompt

    generated = generate_daily_writing_prompt(today_key)
    prompt = WritingPrompt(
        prompt_date=today_key,
        topic=(generated.get('topic') or 'Daily writing').strip(),
        instructions=(generated.get('instructions') or 'Write 120-180 words.').strip(),
        sample_outline=generated.get('sample_outline') or [],
    )
    db.session.add(prompt)
    db.session.commit()
    return prompt


@writing_bp.route('/', methods=['GET'])
@login_required
def practice():
    refresh_prompt = (request.args.get('refresh_prompt') == '1')
    if refresh_prompt:
        allowed, retry_after = check_rate_limit(
            actor_key=f"user:{current_user.id}",
            bucket='writing-prompt-refresh',
            max_requests=4,
            window_seconds=60,
        )
        if not allowed:
            flash(f'Bạn làm mới đề quá nhanh. Vui lòng thử lại sau {retry_after} giây.', 'warning')
            return redirect(url_for('writing.practice'))
        prompt = _refresh_daily_prompt()
        flash('Đã sinh đề viết mới cho hôm nay.', 'success')
    else:
        prompt = _get_or_create_daily_prompt()
    recent_submissions = db.session.execute(
        db.select(WritingSubmission)
        .where(WritingSubmission.user_id == current_user.id)
        .order_by(WritingSubmission.created_at.desc())
        .limit(5)
    ).scalars().all()

    error_counter = Counter()
    for item in recent_submissions:
        for tag in (item.error_tags or []):
            tag_norm = str(tag).strip().lower()
            if tag_norm:
                error_counter[tag_norm] += 1

    top_error_tags = error_counter.most_common(6)
    return render_template(
        'writing.html',
        prompt=prompt,
        recent_submissions=recent_submissions,
        top_error_tags=top_error_tags,
    )


@writing_bp.route('/submit', methods=['POST'])
@login_required
def submit():
    prompt_id = request.form.get('prompt_id', type=int)
    user_text = (request.form.get('essay_text') or '').strip()
    if len(user_text) < 30:
        flash('Bài viết quá ngắn. Vui lòng viết ít nhất 30 ký tự.', 'warning')
        return redirect(url_for('writing.practice'))
    allowed, retry_after = check_rate_limit(
        actor_key=f"user:{current_user.id}",
        bucket='writing-evaluation',
        max_requests=6,
        window_seconds=60,
    )
    if not allowed:
        flash(f'Bạn gửi bài quá nhanh. Vui lòng thử lại sau {retry_after} giây.', 'warning')
        return redirect(url_for('writing.practice'))

    prompt = db.session.get(WritingPrompt, prompt_id) if prompt_id else _get_or_create_daily_prompt()
    if not prompt:
        flash('Không tìm thấy đề bài viết. Vui lòng thử lại.', 'danger')
        return redirect(url_for('writing.practice'))

    evaluation = evaluate_writing_submission(
        topic=prompt.topic,
        instructions=prompt.instructions,
        user_text=user_text,
    )

    submission = WritingSubmission(
        user_id=current_user.id,
        prompt_id=prompt.id,
        submission_type='essay',
        content=user_text,
        score=float(evaluation.get('score') or 0.0),
        corrected_text=(evaluation.get('corrected_text') or '').strip() or user_text,
        feedback_summary=(evaluation.get('feedback_summary') or '').strip(),
        strengths=evaluation.get('strengths') or [],
        improvement_points=evaluation.get('improvement_points') or [],
        error_tags=evaluation.get('error_tags') or [],
    )
    db.session.add(submission)
    reward_summary = apply_writing_rewards(current_user, writing_score=submission.score or 0.0)
    db.session.commit()
    flash(
        f"Đã chấm bài viết thành công. +{reward_summary['xp_gain']} XP (Level {reward_summary['level']}).",
        'success'
    )
    return redirect(url_for('writing.history'))


@writing_bp.route('/history', methods=['GET'])
@login_required
def history():
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        page = 1

    page_size = 5
    base_query = (
        db.select(WritingSubmission)
        .where(WritingSubmission.user_id == current_user.id)
        .order_by(WritingSubmission.created_at.desc())
    )
    all_rows = db.session.execute(base_query).scalars().all()
    total_items = len(all_rows)
    start = (page - 1) * page_size
    end = start + page_size
    rows = all_rows[start:end]

    return render_template(
        'writing_history.html',
        submissions=rows,
        page=page,
        has_prev=page > 1,
        has_more=end < total_items,
    )
