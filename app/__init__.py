from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from app.config import Config
import os

db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = 'auth.login'
login.login_message = 'Vui lòng đăng nhập để truy cập trang này.'
login.login_message_category = 'warning'

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)

    # Import models before any schema initialization so SQLAlchemy metadata is populated.
    from app import models

    # Đăng ký Blueprints
    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.exercises import exercises_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(exercises_bp, url_prefix='/exercises')

    # Start background prefetcher only when explicitly enabled.
    if os.environ.get('ENABLE_EXERCISE_PREFETCH', 'false').lower() == 'true':
        try:
            from app.queue_service import start_prefetcher
            start_prefetcher(app)
        except Exception:
            # Fail silently; prefetcher is an optimization only
            pass

    with app.app_context():
        db.create_all()
        models.ensure_user_schema()
        models.ensure_topic_schema()
        models.ensure_topic_progress_schema()
        models.ensure_subtopic_progress_schema()
        models.ensure_roadmap_cache_schema()
        if app.config.get('RESET_TOPICS_ON_STARTUP', False):
            models.refresh_topics_from_ai(
                wipe_learning_data=app.config.get('WIPE_LEARNING_DATA_ON_TOPIC_RESET', False)
            )
        else:
            models.seed_default_topics()

    return app
