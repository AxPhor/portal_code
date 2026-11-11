from flask import Flask, g
from flask_wtf.csrf import CSRFProtect
from config import SECRET_KEY, MAX_CONTENT_LENGTH
from utils import close_db, current_user

app = Flask(__name__)
app.config.update(SECRET_KEY=SECRET_KEY, MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH)
csrf = CSRFProtect(app)

# Подключаем обработчики БД
app.teardown_appcontext(close_db)

# Контекстный процессор для шаблонов (current_username, current_role)
@app.context_processor
def inject_user():
    u, r = current_user()
    return dict(current_username=u, current_role=r)

# Импортируем и регистрируем Blueprints
from routes_auth import auth_bp
from routes_main import main_bp
from routes_chat import chat_bp

app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(chat_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)