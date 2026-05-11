from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, Text, JSON, Boolean, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import db, login

def seed_default_topics() -> None:
    """Seed topic catalog from AI when the database is empty."""
    if db.session.query(Topic.id).first() is not None:
        return

    try:
        from app.llm_service import generate_topic_suggestions

        topic_suggestions = generate_topic_suggestions()
    except Exception:
        topic_suggestions = [
            {"name": "Conditionals", "description": "Type 0, 1, 2, and 3 conditionals", "topic_type": "grammar"},
            {"name": "Passive Voice", "description": "Passive structures across tenses", "topic_type": "grammar"},
            {"name": "Daily Vocabulary", "description": "High-frequency words for daily communication", "topic_type": "vocabulary"},
        ]

    for topic_data in topic_suggestions:
        name = (topic_data.get("name") or "").strip()
        if not name:
            continue
        description = (topic_data.get("description") or "").strip() or None
        topic_type = (topic_data.get("topic_type") or "grammar").strip().lower()
        if topic_type not in {"grammar", "vocabulary"}:
            topic_type = "grammar"
        db.session.add(Topic(name=name, description=description, topic_type=topic_type))

    db.session.commit()


def refresh_topics_from_ai(wipe_learning_data: bool = False) -> None:
    """Refresh topics from AI. By default, keep learning data intact."""
    if wipe_learning_data:
        db.session.query(ExerciseHistory).delete(synchronize_session=False)
        db.session.query(UserTopicProgress).delete(synchronize_session=False)
        db.session.query(Topic).delete(synchronize_session=False)
        db.session.commit()
        seed_default_topics()
        return

    try:
        from app.llm_service import generate_topic_suggestions
        topic_suggestions = generate_topic_suggestions()
    except Exception:
        topic_suggestions = []

    if not topic_suggestions:
        return

    existing_by_name = {
        topic.name: topic
        for topic in db.session.execute(db.select(Topic)).scalars().all()
    }
    created = False
    updated = False
    for topic_data in topic_suggestions:
        name = (topic_data.get("name") or "").strip()
        if not name:
            continue
        description = (topic_data.get("description") or "").strip() or None
        topic_type = (topic_data.get("topic_type") or "grammar").strip().lower()
        if topic_type not in {"grammar", "vocabulary"}:
            topic_type = "grammar"
        current = existing_by_name.get(name)
        if current is None:
            db.session.add(Topic(name=name, description=description, topic_type=topic_type))
            created = True
        else:
            if description and current.description != description:
                current.description = description
                updated = True
            if current.topic_type != topic_type:
                current.topic_type = topic_type
                updated = True

    if created or updated:
        db.session.commit()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    email: Mapped[Optional[str]] = mapped_column(String(120), index=True, unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(256))
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_study_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_threshold: Mapped[float] = mapped_column(Float, default=0.4)
    xp_points: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    total_correct_answers: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    progress: Mapped[list["UserTopicProgress"]] = relationship("UserTopicProgress", back_populates="user")
    exercises: Mapped[list["ExerciseHistory"]] = relationship("ExerciseHistory", back_populates="user")
    subtopic_progress: Mapped[list["UserSubtopicProgress"]] = relationship("UserSubtopicProgress", back_populates="user")
    roadmap_cache: Mapped[Optional["UserRoadmapCache"]] = relationship("UserRoadmapCache", back_populates="user", uselist=False)
    writing_submissions: Mapped[list["WritingSubmission"]] = relationship("WritingSubmission", back_populates="user")
    flashcard_sets: Mapped[list["FlashcardSet"]] = relationship("FlashcardSet", back_populates="user")
    flashcards: Mapped[list["Flashcard"]] = relationship("Flashcard", back_populates="user")
    xp_events: Mapped[list["XPEvent"]] = relationship("XPEvent", back_populates="user")
    user_badges: Mapped[list["UserBadge"]] = relationship("UserBadge", back_populates="user")
    daily_missions: Mapped[list["DailyMission"]] = relationship("DailyMission", back_populates="user")
    chat_sessions: Mapped[list["ChatSession"]] = relationship("ChatSession", back_populates="user")
    chat_messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="user")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def update_streak(self):
        now = datetime.now(timezone.utc)
        if not self.last_study_date:
            self.streak_days = 1
        else:
            delta = now.date() - self.last_study_date.date()
            if delta.days == 1:
                self.streak_days += 1
            elif delta.days > 1:
                self.streak_days = 1 # Reset if missed a day
        self.last_study_date = now

@login.user_loader
def load_user(id: str):
    return db.session.get(User, int(id))

class Topic(db.Model):
    __tablename__ = 'topics'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    topic_type: Mapped[str] = mapped_column(String(32), default='grammar', server_default='grammar')

    progress_records: Mapped[list["UserTopicProgress"]] = relationship("UserTopicProgress", back_populates="topic")
    subtopic_records: Mapped[list["UserSubtopicProgress"]] = relationship("UserSubtopicProgress", back_populates="topic")

class UserTopicProgress(db.Model):
    __tablename__ = 'user_topic_progress'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'), index=True)
    
    # Tracking
    total_attempted: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    error_rate: Mapped[float] = mapped_column(Float, default=1.0) # 1.0 means 100% error rate initially
    consecutive_high_scores: Mapped[int] = mapped_column(Integer, default=0)
    
    # SM-2 attributes
    easiness_factor: Mapped[float] = mapped_column(Float, default=2.5)
    interval: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    next_review_date: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="progress")
    topic: Mapped["Topic"] = relationship("Topic", back_populates="progress_records")


class UserSubtopicProgress(db.Model):
    __tablename__ = 'user_subtopic_progress'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'), index=True)
    question_type: Mapped[str] = mapped_column(String(32), default='grammar')
    subtopic: Mapped[str] = mapped_column(String(128), default='General')

    total_attempted: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    error_rate: Mapped[float] = mapped_column(Float, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="subtopic_progress")
    topic: Mapped["Topic"] = relationship("Topic", back_populates="subtopic_records")


class UserRoadmapCache(db.Model):
    __tablename__ = 'user_roadmap_cache'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True, unique=True)
    grammar_roadmap: Mapped[list] = mapped_column(JSON, default=list)
    vocabulary_roadmap: Mapped[list] = mapped_column(JSON, default=list)
    personalized_week_plan: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    weekly_plan_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    user: Mapped["User"] = relationship("User", back_populates="roadmap_cache")


class WritingPrompt(db.Model):
    __tablename__ = 'writing_prompts'
    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_date: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    topic: Mapped[str] = mapped_column(String(255))
    instructions: Mapped[str] = mapped_column(Text)
    sample_outline: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    submissions: Mapped[list["WritingSubmission"]] = relationship("WritingSubmission", back_populates="prompt")


class WritingSubmission(db.Model):
    __tablename__ = 'writing_submissions'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    prompt_id: Mapped[Optional[int]] = mapped_column(ForeignKey('writing_prompts.id'), index=True)
    submission_type: Mapped[str] = mapped_column(String(32), default='essay')
    content: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    corrected_text: Mapped[Optional[str]] = mapped_column(Text)
    feedback_summary: Mapped[Optional[str]] = mapped_column(Text)
    strengths: Mapped[list] = mapped_column(JSON, default=list)
    improvement_points: Mapped[list] = mapped_column(JSON, default=list)
    error_tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="writing_submissions")
    prompt: Mapped[Optional["WritingPrompt"]] = relationship("WritingPrompt", back_populates="submissions")


class FlashcardSet(db.Model):
    __tablename__ = 'flashcard_sets'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    title: Mapped[str] = mapped_column(String(255))
    source_topic: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="flashcard_sets")
    cards: Mapped[list["Flashcard"]] = relationship("Flashcard", back_populates="card_set")


class Flashcard(db.Model):
    __tablename__ = 'flashcards'
    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey('flashcard_sets.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    term: Mapped[str] = mapped_column(String(255))
    definition: Mapped[str] = mapped_column(Text)
    vietnamese_meaning: Mapped[Optional[str]] = mapped_column(Text)
    part_of_speech: Mapped[Optional[str]] = mapped_column(String(32))
    ipa_pronunciation: Mapped[Optional[str]] = mapped_column(String(64))
    example_sentence: Mapped[Optional[str]] = mapped_column(Text)
    example_sentence_2: Mapped[Optional[str]] = mapped_column(Text)
    pronunciation_hint: Mapped[Optional[str]] = mapped_column(String(255))
    image_hint: Mapped[Optional[str]] = mapped_column(String(255))
    image_url: Mapped[Optional[str]] = mapped_column(String(512))
    easiness_factor: Mapped[float] = mapped_column(Float, default=2.5)
    interval: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    next_review_date: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    last_quality: Mapped[Optional[int]] = mapped_column(Integer)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    card_set: Mapped["FlashcardSet"] = relationship("FlashcardSet", back_populates="cards")
    user: Mapped["User"] = relationship("User", back_populates="flashcards")


def ensure_topic_progress_schema() -> None:
    """Add missing columns needed by newer learning rules to existing SQLite tables."""
    try:
        engine = db.engine
        if engine.dialect.name != 'sqlite':
            return

        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(user_topic_progress)"))
            }
            if 'consecutive_high_scores' not in columns:
                connection.execute(text(
                    "ALTER TABLE user_topic_progress ADD COLUMN consecutive_high_scores INTEGER DEFAULT 0"
                ))
                connection.commit()
    except Exception:
        # If the migration helper fails, the app can still run with the ORM default
        pass


def ensure_user_schema() -> None:
    """Add user settings columns introduced after initial deployment."""
    try:
        engine = db.engine
        if engine.dialect.name != 'sqlite':
            return

        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(users)"))
            }
            if 'error_threshold' not in columns:
                connection.execute(text(
                    "ALTER TABLE users ADD COLUMN error_threshold FLOAT DEFAULT 0.4"
                ))
                connection.commit()
            if 'xp_points' not in columns:
                connection.execute(text(
                    "ALTER TABLE users ADD COLUMN xp_points INTEGER DEFAULT 0"
                ))
                connection.commit()
            if 'level' not in columns:
                connection.execute(text(
                    "ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1"
                ))
                connection.commit()
            if 'total_correct_answers' not in columns:
                connection.execute(text(
                    "ALTER TABLE users ADD COLUMN total_correct_answers INTEGER DEFAULT 0"
                ))
                connection.commit()
            if 'email' not in columns:
                connection.execute(text(
                    "ALTER TABLE users ADD COLUMN email VARCHAR(120)"
                ))
                connection.commit()
    except Exception:
        pass


def ensure_topic_schema() -> None:
    """Add topic catalog columns introduced after initial deployment."""
    try:
        engine = db.engine
        if engine.dialect.name != 'sqlite':
            return

        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(topics)"))
            }
            if 'topic_type' not in columns:
                connection.execute(text(
                    "ALTER TABLE topics ADD COLUMN topic_type VARCHAR(32) DEFAULT 'grammar'"
                ))
                connection.commit()
    except Exception:
        pass


def ensure_subtopic_progress_schema() -> None:
    """Create subtopic progress table if it does not exist (SQLite only helper)."""
    try:
        engine = db.engine
        if engine.dialect.name != 'sqlite':
            return

        with engine.connect() as connection:
            exists = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_subtopic_progress'"
            )).fetchone()
            if not exists:
                connection.execute(text(
                    """
                    CREATE TABLE user_subtopic_progress (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        topic_id INTEGER NOT NULL,
                        question_type VARCHAR(32) DEFAULT 'grammar',
                        subtopic VARCHAR(128) DEFAULT 'General',
                        total_attempted INTEGER DEFAULT 0,
                        correct_count INTEGER DEFAULT 0,
                        error_rate FLOAT DEFAULT 1.0,
                        updated_at DATETIME,
                        FOREIGN KEY(user_id) REFERENCES users (id),
                        FOREIGN KEY(topic_id) REFERENCES topics (id)
                    )
                    """
                ))
                connection.execute(text(
                    "CREATE INDEX ix_user_subtopic_progress_user_id ON user_subtopic_progress (user_id)"
                ))
                connection.execute(text(
                    "CREATE INDEX ix_user_subtopic_progress_topic_id ON user_subtopic_progress (topic_id)"
                ))
                connection.commit()
    except Exception:
        pass


def ensure_roadmap_cache_schema() -> None:
    """Create roadmap cache table if it does not exist (SQLite only helper)."""
    try:
        engine = db.engine
        if engine.dialect.name != 'sqlite':
            return

        with engine.connect() as connection:
            exists = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_roadmap_cache'"
            )).fetchone()
            if not exists:
                connection.execute(text(
                    """
                    CREATE TABLE user_roadmap_cache (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER NOT NULL UNIQUE,
                        grammar_roadmap JSON,
                        vocabulary_roadmap JSON,
                        personalized_week_plan JSON,
                        updated_at DATETIME,
                        weekly_plan_updated_at DATETIME,
                        FOREIGN KEY(user_id) REFERENCES users (id)
                    )
                    """
                ))
                connection.execute(text(
                    "CREATE INDEX ix_user_roadmap_cache_user_id ON user_roadmap_cache (user_id)"
                ))
                connection.commit()
            else:
                columns = {
                    row[1]
                    for row in connection.execute(text("PRAGMA table_info(user_roadmap_cache)"))
                }
                if 'personalized_week_plan' not in columns:
                    connection.execute(text(
                        "ALTER TABLE user_roadmap_cache ADD COLUMN personalized_week_plan JSON"
                    ))
                    connection.commit()
                if 'weekly_plan_updated_at' not in columns:
                    connection.execute(text(
                        "ALTER TABLE user_roadmap_cache ADD COLUMN weekly_plan_updated_at DATETIME"
                    ))
                    connection.commit()
    except Exception:
        pass


def ensure_flashcard_schema() -> None:
    """Ensure flashcards table has extended fields (SQLite only helper)."""
    try:
        engine = db.engine
        if engine.dialect.name != 'sqlite':
            return

        with engine.connect() as connection:
            exists = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='flashcards'"
            )).fetchone()
            if not exists:
                return
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(flashcards)"))
            }
            if 'vietnamese_meaning' not in columns:
                connection.execute(text(
                    "ALTER TABLE flashcards ADD COLUMN vietnamese_meaning TEXT"
                ))
                connection.commit()
            if 'part_of_speech' not in columns:
                connection.execute(text(
                    "ALTER TABLE flashcards ADD COLUMN part_of_speech VARCHAR(32)"
                ))
                connection.commit()
            if 'ipa_pronunciation' not in columns:
                connection.execute(text(
                    "ALTER TABLE flashcards ADD COLUMN ipa_pronunciation VARCHAR(64)"
                ))
                connection.commit()
            if 'example_sentence_2' not in columns:
                connection.execute(text(
                    "ALTER TABLE flashcards ADD COLUMN example_sentence_2 TEXT"
                ))
                connection.commit()
            if 'image_url' not in columns:
                connection.execute(text(
                    "ALTER TABLE flashcards ADD COLUMN image_url VARCHAR(512)"
                ))
                connection.commit()
    except Exception:
        pass

class ExerciseHistory(db.Model):
    __tablename__ = 'exercise_history'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Raw JSON of questions and user answers
    exercise_data: Mapped[dict] = mapped_column(JSON)
    score: Mapped[int] = mapped_column(Integer) # How many correct out of total
    total_questions: Mapped[int] = mapped_column(Integer)

    user: Mapped["User"] = relationship("User", back_populates="exercises")


class XPEvent(db.Model):
    __tablename__ = 'xp_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64), default='activity')
    description: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user: Mapped["User"] = relationship("User", back_populates="xp_events")


class Badge(db.Model):
    __tablename__ = 'badges'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(255))
    icon: Mapped[str] = mapped_column(String(32), default='award')

    users: Mapped[list["UserBadge"]] = relationship("UserBadge", back_populates="badge")


class UserBadge(db.Model):
    __tablename__ = 'user_badges'
    __table_args__ = (UniqueConstraint('user_id', 'badge_id', name='uq_user_badge'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    badge_id: Mapped[int] = mapped_column(ForeignKey('badges.id'), index=True)
    awarded_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="user_badges")
    badge: Mapped["Badge"] = relationship("Badge", back_populates="users")


class DailyMission(db.Model):
    __tablename__ = 'daily_missions'
    __table_args__ = (UniqueConstraint('user_id', 'mission_date', 'mission_type', name='uq_user_mission_day_type'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    mission_date: Mapped[str] = mapped_column(String(10), index=True)
    mission_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    target_value: Mapped[int] = mapped_column(Integer, default=1)
    progress_value: Mapped[int] = mapped_column(Integer, default=0)
    reward_xp: Mapped[int] = mapped_column(Integer, default=20)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="daily_missions")


class ChatSession(db.Model):
    __tablename__ = 'chat_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    summary_error_tags: Mapped[list] = mapped_column(JSON, default=list)

    user: Mapped["User"] = relationship("User", back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="session")


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('chat_sessions.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    corrections: Mapped[list] = mapped_column(JSON, default=list)
    error_tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")
    user: Mapped["User"] = relationship("User", back_populates="chat_messages")


def seed_default_badges() -> None:
    badge_defs = [
        {"code": "streak_7", "name": "Streak 7 ngày", "description": "Học liên tiếp 7 ngày", "icon": "fire"},
        {"code": "correct_100", "name": "100 câu đúng", "description": "Tích lũy 100 câu trả lời đúng", "icon": "check2-circle"},
        {"code": "perfect_quiz", "name": "Điểm tuyệt đối", "description": "Đạt điểm tối đa trong một bài luyện tập", "icon": "stars"},
        {"code": "writing_10", "name": "Writer chăm chỉ", "description": "Hoàn thành 10 bài luyện viết", "icon": "pencil-square"},
        {"code": "flashcard_50", "name": "Flashcard grinder", "description": "Ôn 50 lần flashcard", "icon": "collection"},
    ]
    existing_codes = {
        b.code for b in db.session.execute(db.select(Badge)).scalars().all()
    }
    created = False
    for badge in badge_defs:
        if badge["code"] in existing_codes:
            continue
        db.session.add(Badge(**badge))
        created = True
    if created:
        db.session.commit()
