import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.dirname(basedir)
# Load parent directory's .env file
load_dotenv(os.path.join(project_root, '.env'))

default_db_dir = os.environ.get('DATABASE_DIR') or os.path.join(project_root, 'instance')
default_db_path = os.path.join(default_db_dir, 'app.db')

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + default_db_path
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    RESET_TOPICS_ON_STARTUP = os.environ.get('RESET_TOPICS_ON_STARTUP', 'false').lower() == 'true'
    WIPE_LEARNING_DATA_ON_TOPIC_RESET = os.environ.get('WIPE_LEARNING_DATA_ON_TOPIC_RESET', 'false').lower() == 'true'