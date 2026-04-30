from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, Text, JSON, Boolean, text
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
    password_hash: Mapped[Optional[str]] = mapped_column(String(256))
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_study_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_threshold: Mapped[float] = mapped_column(Float, default=0.4)

    # Relationships
    progress: Mapped[list["UserTopicProgress"]] = relationship("UserTopicProgress", back_populates="user")
    exercises: Mapped[list["ExerciseHistory"]] = relationship("ExerciseHistory", back_populates="user")
    subtopic_progress: Mapped[list["UserSubtopicProgress"]] = relationship("UserSubtopicProgress", back_populates="user")
    roadmap_cache: Mapped[Optional["UserRoadmapCache"]] = relationship("UserRoadmapCache", back_populates="user", uselist=False)

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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="roadmap_cache")


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
                        updated_at DATETIME,
                        FOREIGN KEY(user_id) REFERENCES users (id)
                    )
                    """
                ))
                connection.execute(text(
                    "CREATE INDEX ix_user_roadmap_cache_user_id ON user_roadmap_cache (user_id)"
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
