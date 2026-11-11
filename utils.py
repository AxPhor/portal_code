import os, sqlite3, functools
from datetime import datetime, timedelta
from flask import g, session, redirect, url_for, request
from config import DATABASE, CHAT_UPLOAD_DIR

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def current_user():
    return session.get("username"), session.get("role")

def login_required(roles=("admin", "editor")):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if session.get("role") not in roles:
                return redirect(url_for("auth.login", next=request.path))
            return fn(*a, **kw)
        return wrapper
    return deco

def log_action(action, document_id=None, details=""):
    u, _ = current_user()
    if not u:
        return
    db = get_db()
    db.execute("INSERT INTO audit_log(username, action, document_id, details) VALUES (?,?,?,?)",
               (u, action, document_id, details))
    db.commit()

def cleanup_chat_data():
    db = get_db()
    now = datetime.now()
    
    keep_msgs = db.execute("SELECT value FROM settings WHERE key='chat_keep_messages_days'").fetchone()
    keep_files = db.execute("SELECT value FROM settings WHERE key='chat_keep_files_days'").fetchone()
    
    msgs_days = int(keep_msgs["value"]) if keep_msgs else 14
    files_days = int(keep_files["value"]) if keep_files else 7
    
    limit_msgs = (now - timedelta(days=msgs_days)).strftime("%Y-%m-%d %H:%M:%S")
    old_messages = db.execute("SELECT id, file_path FROM messages WHERE created_at < ?", (limit_msgs,)).fetchall()
    
    for msg in old_messages:
        if msg["file_path"]:
            file_to_delete = os.path.join(CHAT_UPLOAD_DIR, msg["file_path"])
            if os.path.exists(file_to_delete):
                try: os.remove(file_to_delete)
                except OSError: pass
    db.execute("DELETE FROM messages WHERE created_at < ?", (limit_msgs,))
    
    limit_files = (now - timedelta(days=files_days)).strftime("%Y-%m-%d %H:%M:%S")
    old_files = db.execute("SELECT id, file_path FROM messages WHERE created_at < ? AND file_path IS NOT NULL", (limit_files,)).fetchall()
    
    for msg in old_files:
        file_to_delete = os.path.join(CHAT_UPLOAD_DIR, msg["file_path"])
        if os.path.exists(file_to_delete):
            try: os.remove(file_to_delete)
            except OSError: pass
        db.execute("UPDATE messages SET file_path = NULL, file_original_name = NULL WHERE id = ?", (msg["id"],))
        
    db.commit()