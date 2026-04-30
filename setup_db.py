import os
from app import create_app, db
from app import models

app = create_app()
with app.app_context():
    sqlite_uri_prefix = 'sqlite:///'
    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if db_uri.startswith(sqlite_uri_prefix):
        db_path = db_uri[len(sqlite_uri_prefix):]
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    db.create_all()
    print("Database created successfully without migration overhead for MVP!")
