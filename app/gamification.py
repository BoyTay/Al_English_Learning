from datetime import datetime, timedelta, timezone

from app import db
from app.models import Badge, DailyMission, Flashcard, User, UserBadge, WritingSubmission, XPEvent


def xp_to_level(total_xp: int) -> int:
    return max(1, (int(total_xp or 0) // 100) + 1)


def award_xp(user: User, amount: int, source: str, description: str | None = None) -> int:
    xp = max(0, int(amount or 0))
    if xp <= 0:
        return 0
    user.xp_points = int(user.xp_points or 0) + xp
    user.level = xp_to_level(user.xp_points)
    db.session.add(XPEvent(
        user_id=user.id,
        amount=xp,
        source=source,
        description=description,
    ))
    return xp


def ensure_daily_missions(user: User) -> list[DailyMission]:
    today_key = datetime.now(timezone.utc).date().isoformat()
    missions = db.session.execute(
        db.select(DailyMission).where(
            DailyMission.user_id == user.id,
            DailyMission.mission_date == today_key,
        )
    ).scalars().all()
    if missions:
        return sorted(missions, key=lambda m: m.completed)

    templates = [
        ("exercise_complete", "Hoàn thành 1 bài luyện tập", 1, 20),
        ("writing_submission", "Nộp 1 bài viết", 1, 25),
        ("correct_answers", "Trả lời đúng 10 câu", 10, 30),
    ]
    for mission_type, title, target, reward_xp in templates:
        db.session.add(DailyMission(
            user_id=user.id,
            mission_date=today_key,
            mission_type=mission_type,
            title=title,
            target_value=target,
            progress_value=0,
            reward_xp=reward_xp,
            completed=False,
        ))
    db.session.flush()
    missions = db.session.execute(
        db.select(DailyMission).where(
            DailyMission.user_id == user.id,
            DailyMission.mission_date == today_key,
        )
    ).scalars().all()
    return sorted(missions, key=lambda m: m.completed)


def add_mission_progress(user: User, mission_type: str, amount: int) -> int:
    if amount <= 0:
        return 0
    missions = ensure_daily_missions(user)
    now = datetime.now(timezone.utc)
    rewarded = 0
    for mission in missions:
        if mission.mission_type != mission_type or mission.completed:
            continue
        mission.progress_value = min(
            int(mission.target_value or 1),
            int(mission.progress_value or 0) + int(amount),
        )
        if mission.progress_value >= int(mission.target_value or 1):
            mission.completed = True
            mission.completed_at = now
            rewarded += award_xp(
                user,
                int(mission.reward_xp or 0),
                source="daily_mission",
                description=mission.title,
            )
    return rewarded


def _award_badge_if_missing(user: User, badge_code: str, bucket: list[Badge]) -> None:
    badge = db.session.scalar(db.select(Badge).where(Badge.code == badge_code))
    if not badge:
        return
    exists = db.session.scalar(
        db.select(UserBadge).where(
            UserBadge.user_id == user.id,
            UserBadge.badge_id == badge.id,
        )
    )
    if exists:
        return
    db.session.add(UserBadge(user_id=user.id, badge_id=badge.id))
    bucket.append(badge)


def evaluate_badges(
    user: User,
    latest_score: int | None = None,
    latest_total: int | None = None,
) -> list[Badge]:
    awarded: list[Badge] = []
    if int(user.streak_days or 0) >= 7:
        _award_badge_if_missing(user, "streak_7", awarded)
    if int(user.total_correct_answers or 0) >= 100:
        _award_badge_if_missing(user, "correct_100", awarded)
    if latest_total and latest_score is not None and latest_total > 0 and latest_score >= latest_total:
        _award_badge_if_missing(user, "perfect_quiz", awarded)

    writing_count = db.session.scalar(
        db.select(db.func.count(WritingSubmission.id)).where(WritingSubmission.user_id == user.id)
    ) or 0
    if int(writing_count) >= 10:
        _award_badge_if_missing(user, "writing_10", awarded)

    flash_review_sum = db.session.scalar(
        db.select(db.func.sum(Flashcard.review_count)).where(Flashcard.user_id == user.id)
    ) or 0
    if int(flash_review_sum) >= 50:
        _award_badge_if_missing(user, "flashcard_50", awarded)
    return awarded


def apply_exercise_rewards(user: User, score: int, total: int, topic_name: str | None = None) -> dict:
    score = max(0, int(score or 0))
    total = max(0, int(total or 0))
    user.total_correct_answers = int(user.total_correct_answers or 0) + score
    base_xp = (score * 3) + (5 if total > 0 and score >= total else 0)
    xp_gain = award_xp(user, base_xp, source="exercise", description=topic_name or "exercise")
    mission_xp = 0
    mission_xp += add_mission_progress(user, "exercise_complete", 1)
    mission_xp += add_mission_progress(user, "correct_answers", score)
    badges = evaluate_badges(user, latest_score=score, latest_total=total)
    return {
        "xp_gain": xp_gain + mission_xp,
        "level": int(user.level or 1),
        "mission_bonus_xp": mission_xp,
        "awarded_badges": [b.name for b in badges],
    }


def apply_writing_rewards(user: User, writing_score: float) -> dict:
    base = max(8, int(float(writing_score or 0.0) * 2.5))
    xp_gain = award_xp(user, base, source="writing", description="writing submission")
    mission_xp = add_mission_progress(user, "writing_submission", 1)
    badges = evaluate_badges(user)
    return {
        "xp_gain": xp_gain + mission_xp,
        "level": int(user.level or 1),
        "mission_bonus_xp": mission_xp,
        "awarded_badges": [b.name for b in badges],
    }


def apply_flashcard_rewards(user: User, quality: int) -> dict:
    quality = max(0, min(5, int(quality or 0)))
    base = 1 if quality <= 2 else (2 if quality <= 4 else 3)
    xp_gain = award_xp(user, base, source="flashcard_review", description=f"quality={quality}")
    badges = evaluate_badges(user)
    return {
        "xp_gain": xp_gain,
        "level": int(user.level or 1),
        "awarded_badges": [b.name for b in badges],
    }


def get_recent_badges(user: User, limit: int = 6) -> list[UserBadge]:
    return db.session.execute(
        db.select(UserBadge)
        .where(UserBadge.user_id == user.id)
        .order_by(UserBadge.awarded_at.desc())
        .limit(limit)
    ).scalars().all()


def get_weekly_leaderboard(limit: int = 10) -> list[dict]:
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    xp_sum = db.func.coalesce(db.func.sum(XPEvent.amount), 0)
    rows = db.session.execute(
        db.select(
            User.id,
            User.username,
            xp_sum.label("xp"),
            User.level,
        )
        .join(XPEvent, XPEvent.user_id == User.id, isouter=True)
        .where((XPEvent.created_at.is_(None)) | (XPEvent.created_at >= week_start))
        .group_by(User.id, User.username, User.level)
        .order_by(xp_sum.desc(), User.username.asc())
        .limit(limit)
    ).all()
    results: list[dict] = []
    for rank, row in enumerate(rows, start=1):
        results.append({
            "rank": rank,
            "user_id": row[0],
            "username": row[1],
            "xp": int(row[2] or 0),
            "level": int(row[3] or 1),
        })
    return results
