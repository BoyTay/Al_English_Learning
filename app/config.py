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
    
    # Mail Config
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', MAIL_USERNAME)