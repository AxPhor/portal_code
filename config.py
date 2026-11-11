import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = "change-me-to-a-long-random-string"

DATABASE = os.path.join(BASE_DIR, "portal.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)  # <-- Добавлена эта строка
ALLOWED_EXT = {"pdf"}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 МБ

# Настройки почты (SMTP) хранятся в базе данных, таблица settings.
# Здесь их дублировать НЕ нужно — см. /admin/settings/mail в админке.

# Настройки мессенджера
CHAT_UPLOAD_DIR = os.path.join(BASE_DIR, "uploads", "chat")
os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)
CHAT_MAX_FILE_SIZE = 30 * 1024 * 1024  # 30 МБ

# Папка для стандартных аватаров
AVATARS_DIR = os.path.join(CHAT_UPLOAD_DIR, "avatars")
os.makedirs(AVATARS_DIR, exist_ok=True)
