from app import create_app, db
from app.models import User, Topic, UserTopicProgress, UserSubtopicProgress

app = create_app()

@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'User': User,
        'Topic': Topic,
        'UserTopicProgress': UserTopicProgress,
        'UserSubtopicProgress': UserSubtopicProgress,
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5000)
