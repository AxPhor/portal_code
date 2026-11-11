import os, uuid
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, send_from_directory, abort
from werkzeug.security import check_password_hash, generate_password_hash
from utils import get_db, log_action, cleanup_chat_data
from config import CHAT_UPLOAD_DIR, AVATARS_DIR

chat_bp = Blueprint('chat', __name__, url_prefix='/chat')

@chat_bp.route("/", methods=["GET", "POST"])
def chat_home():
    cleanup_chat_data()
    if session.get("chat_user_id"):
        return render_template("chat.html")
        
    if request.method == "POST":
        emp_id = request.form.get("employee_id"); password = request.form.get("password")
        if not emp_id or not password:
            flash("Выберите сотрудника и введите пароль", "danger"); return redirect(url_for("chat.chat_home"))
        db = get_db()
        emp = db.execute("SELECT * FROM employees WHERE id=? AND chat_enabled=1", (emp_id,)).fetchone()
        if emp and emp["chat_password_hash"] and check_password_hash(emp["chat_password_hash"], password):
            session["chat_user_id"] = emp["id"]; session["chat_user_name"] = emp["full_name"]
            return redirect(url_for("chat.chat_home"))
        else:
            flash("Неверный пароль или доступ к мессенджеру запрещен", "danger")
            
    db = get_db()
    employees = db.execute("SELECT id, full_name FROM employees WHERE chat_enabled=1 AND is_deleted=0 ORDER BY full_name").fetchall()
    return render_template("chat_login.html", employees=employees)

@chat_bp.route("/logout")
def chat_logout():
    session.pop("chat_user_id", None); session.pop("chat_user_name", None)
    return redirect(url_for("chat.chat_home"))

# --- Загрузка аватара и получение своего аватара ---
@chat_bp.route("/upload_avatar", methods=["POST"])
def chat_upload_avatar():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]
    
    file = request.files.get("avatar")
    if not file or file.filename == "": return {"error": "Файл не выбран"}, 400
        
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ("jpg", "jpeg"): return {"error": "Разрешены только JPG"}, 400
        
    stored_name = f"user_{uuid.uuid4().hex}.jpg"
    file.save(os.path.join(AVATARS_DIR, stored_name))
    
    db = get_db()
    old = db.execute("SELECT avatar_path FROM employees WHERE id=?", (user_id,)).fetchone()
    if old and old["avatar_path"]:
        if old["avatar_path"].startswith("avatars/"):
            old_file = old["avatar_path"].replace("avatars/", "")
            old_path = os.path.join(AVATARS_DIR, old_file)
        else:
            old_path = os.path.join(CHAT_UPLOAD_DIR, old["avatar_path"])
        if os.path.exists(old_path):
            try: os.remove(old_path)
            except OSError: pass
            
    db.execute("UPDATE employees SET avatar_path=? WHERE id=?", ("avatars/" + stored_name, user_id))
    db.commit()
    
    return {"status": "ok"}

@chat_bp.route("/api/my_avatar")
def chat_api_my_avatar():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]
    db = get_db()
    row = db.execute("SELECT avatar_path FROM employees WHERE id=?", (user_id,)).fetchone()
    if row and row["avatar_path"]:
        return {"avatar_path": row["avatar_path"]}
    return {"avatar_path": None}

@chat_bp.route("/api/default_avatars")
def chat_api_default_avatars():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    files = []
    if os.path.exists(AVATARS_DIR):
        for f in os.listdir(AVATARS_DIR):
            if f.lower().endswith(".jpg"):
                files.append(f)
    files.sort()
    return {"avatars": files}

@chat_bp.route("/set_default_avatar", methods=["POST"])
def chat_set_default_avatar():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]
    filename = request.form.get("avatar_filename")
    
    if not filename:
        return {"error": "Файл не указан"}, 400
        
    file_path = os.path.join(AVATARS_DIR, filename)
    if not os.path.exists(file_path) or not filename.lower().endswith(".jpg"):
        return {"error": "Недопустимый файл"}, 400
        
    db = get_db()
    db.execute("UPDATE employees SET avatar_path=? WHERE id=?", ("avatars/" + filename, user_id))
    db.commit()
    
    return {"status": "ok"}

@chat_bp.route("/avatar/<path:filename>")
def chat_avatar(filename):
    if filename.startswith("avatars/"):
        real_filename = filename.replace("avatars/", "")
        return send_from_directory(AVATARS_DIR, real_filename)
    return send_from_directory(CHAT_UPLOAD_DIR, filename)

@chat_bp.route("/api/offline", methods=["POST"])
def chat_api_offline():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]
    db = get_db()
    old_time = (datetime.utcnow() + timedelta(hours=5) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE online_status SET last_seen_at = ? WHERE employee_id = ?", (old_time, user_id))
    db.commit()
    return {"status": "ok"}

# --- API чатов ---
@chat_bp.route("/api/chats")
def chat_api_chats():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]; db = get_db()
    
    users = db.execute("""
        SELECT e.id, e.full_name, e.position, e.email, e.phone, e.avatar_path,
        (SELECT MAX(created_at) FROM messages WHERE (sender_id = e.id AND receiver_id = ?) OR (sender_id = ? AND receiver_id = e.id)) as last_msg,
        (SELECT text FROM messages WHERE (sender_id = e.id AND receiver_id = ?) OR (sender_id = ? AND receiver_id = e.id) ORDER BY created_at DESC LIMIT 1) as last_text,
        (SELECT sender_id FROM messages WHERE (sender_id = e.id AND receiver_id = ?) OR (sender_id = ? AND receiver_id = e.id) ORDER BY created_at DESC LIMIT 1) as last_sender_id
        FROM employees e
        WHERE e.chat_enabled=1 AND e.id != ? AND e.is_deleted=0
        ORDER BY CASE WHEN last_msg IS NULL THEN 1 ELSE 0 END, last_msg DESC, e.full_name
    """, (user_id, user_id, user_id, user_id, user_id, user_id, user_id)).fetchall()
    
    threshold_time = (datetime.utcnow() + timedelta(hours=5) - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    online_query = db.execute("SELECT employee_id FROM online_status WHERE last_seen_at >= ?", (threshold_time,)).fetchall()
    online_ids = [row["employee_id"] for row in online_query]
    
    groups = db.execute("""
        SELECT g.id, g.name,
        (SELECT MAX(created_at) FROM messages WHERE group_id = g.id) as last_msg,
        (SELECT text FROM messages WHERE group_id = g.id ORDER BY created_at DESC LIMIT 1) as last_text,
        (SELECT e2.full_name FROM messages m2 JOIN employees e2 ON m2.sender_id = e2.id WHERE m2.group_id = g.id ORDER BY m2.created_at DESC LIMIT 1) as last_sender_name
        FROM chat_groups g
        JOIN group_members gm ON g.id = gm.group_id
        WHERE gm.employee_id = ?
        ORDER BY CASE WHEN last_msg IS NULL THEN 1 ELSE 0 END, last_msg DESC, g.name
    """, (user_id,)).fetchall()
    
    def has_unread(chat_id, chat_type):
        read_row = db.execute("SELECT last_read_at FROM chat_reads WHERE user_id=? AND chat_id=? AND chat_type=?", (user_id, chat_id, chat_type)).fetchone()
        last_read = read_row["last_read_at"] if read_row else "1970-01-01 00:00:00"
        if chat_type == "user":
            count = db.execute("SELECT COUNT(*) FROM messages WHERE sender_id=? AND receiver_id=? AND created_at > ?", (chat_id, user_id, last_read)).fetchone()[0]
        else:
            count = db.execute("SELECT COUNT(*) FROM messages WHERE group_id=? AND sender_id != ? AND created_at > ?", (chat_id, user_id, last_read)).fetchone()[0]
        return count > 0

    users_data = []
    for u in users:
        u_dict = dict(u); u_dict["has_unread"] = has_unread(u["id"], "user"); u_dict["is_online"] = (u["id"] in online_ids); users_data.append(u_dict)
        
    groups_data = []
    for g in groups:
        g_dict = dict(g); g_dict["has_unread"] = has_unread(g["id"], "group"); groups_data.append(g_dict)

    return {"users": users_data, "groups": groups_data}

@chat_bp.route("/api/group_members/<int:group_id>")
def chat_api_group_members(group_id):
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    db = get_db()
    threshold_time = (datetime.utcnow() + timedelta(hours=5) - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    members = db.execute("""
        SELECT e.id, e.full_name, e.position, e.email, e.phone, e.avatar_path,
        (SELECT CASE WHEN last_seen_at >= ? THEN 1 ELSE 0 END FROM online_status WHERE employee_id = e.id) as is_online_calc
        FROM group_members gm JOIN employees e ON gm.employee_id = e.id 
        WHERE gm.group_id = ? ORDER BY e.full_name
    """, (threshold_time, group_id)).fetchall()
    members_data = []
    for m in members:
        m_dict = dict(m); m_dict["is_online"] = bool(m_dict.get("is_online_calc")); members_data.append(m_dict)
    return {"members": members_data}

@chat_bp.route("/api/read", methods=["POST"])
def chat_api_read():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]; db = get_db()
    chat_id = request.form.get("chat_id"); chat_type = request.form.get("type", "user")
    local_time = (datetime.utcnow() + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("INSERT OR REPLACE INTO chat_reads (user_id, chat_id, chat_type, last_read_at) VALUES (?, ?, ?, ?)", (user_id, chat_id, chat_type, local_time))
    db.commit()
    return {"status": "ok"}

@chat_bp.route("/api/read_status/<int:chat_id>")
def chat_api_read_status(chat_id):
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]; db = get_db()
    read_row = db.execute("SELECT last_read_at FROM chat_reads WHERE user_id=? AND chat_id=? AND chat_type='user'", (chat_id, user_id)).fetchone()
    if read_row: return {"last_read_at": read_row["last_read_at"]}
    return {"last_read_at": None}

@chat_bp.route("/api/heartbeat", methods=["POST"])
def chat_api_heartbeat():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]; db = get_db()
    local_time = (datetime.utcnow() + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    cur = db.execute("UPDATE online_status SET last_seen_at = ? WHERE employee_id = ?", (local_time, user_id))
    if cur.rowcount == 0:
        db.execute("INSERT INTO online_status (employee_id, last_seen_at) VALUES (?, ?)", (user_id, local_time))
    db.commit()
    return {"status": "ok"}

@chat_bp.route("/api/messages/<int:chat_id>")
def chat_api_messages(chat_id):
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]; db = get_db(); chat_type = request.args.get("type", "user")
    
    if chat_type == "user":
        raw_messages = db.execute("""SELECT m.id, m.text, m.created_at, m.sender_id, m.file_path, m.file_original_name, e.full_name as sender_name, m.file_group_id, e.avatar_path
            FROM messages m JOIN employees e ON m.sender_id = e.id
            WHERE (m.sender_id = ? AND m.receiver_id = ?) OR (m.sender_id = ? AND m.receiver_id = ?)
            ORDER BY m.created_at ASC""", (user_id, chat_id, chat_id, user_id)).fetchall()
    else:
        raw_messages = db.execute("""SELECT m.id, m.text, m.created_at, m.sender_id, m.file_path, m.file_original_name, e.full_name as sender_name, m.file_group_id, e.avatar_path
            FROM messages m JOIN employees e ON m.sender_id = e.id WHERE m.group_id = ? ORDER BY m.created_at ASC""", (chat_id,)).fetchall()
    
    messages = []; file_groups = {}
    for m in raw_messages:
        m_dict = dict(m)
        if m_dict["file_group_id"]:
            if m_dict["file_group_id"] not in file_groups: file_groups[m_dict["file_group_id"]] = []
            file_groups[m_dict["file_group_id"]].append(m_dict)
        else:
            if m_dict["file_path"]: m_dict["files"] = [{"id": m_dict["id"], "name": m_dict["file_original_name"]}]
            else: m_dict["files"] = []
            messages.append(m_dict)
            
    for group_id, file_list in file_groups.items():
        if file_list:
            first_msg = file_list[0]
            first_msg["files"] = [{"id": f["id"], "name": f["file_original_name"]} for f in file_list if f["file_path"]]
            messages.append(first_msg)
            
    messages.sort(key=lambda x: x["created_at"])
    for m in messages:
        if m["files"]: m["file_path"] = None; m["file_original_name"] = None
    return {"messages": messages}

@chat_bp.route("/api/send", methods=["POST"])
def chat_api_send():
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]; db = get_db()
    chat_id = request.form.get("chat_id"); chat_type = request.form.get("type", "user")
    text = request.form.get("text", "").strip(); files = request.files.getlist("file")
    
    if not text and (not files or files[0].filename == ""): return {"error": "Empty message"}, 400
    
    local_time = (datetime.utcnow() + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    file_group_id = None
    
    if files and files[0].filename != "":
        file_group_id = uuid.uuid4().hex
        for i, file in enumerate(files):
            ext = file.filename.rsplit('.', 1)[-1].lower()
            stored_name = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(CHAT_UPLOAD_DIR, stored_name))
            current_text = text if i == 0 else ""
            if chat_type == "user":
                db.execute("INSERT INTO messages (sender_id, receiver_id, text, file_path, file_original_name, created_at, file_group_id) VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, chat_id, current_text, stored_name, file.filename, local_time, file_group_id))
            else:
                db.execute("INSERT INTO messages (sender_id, group_id, text, file_path, file_original_name, created_at, file_group_id) VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, chat_id, current_text, stored_name, file.filename, local_time, file_group_id))
    else:
        if chat_type == "user":
            db.execute("INSERT INTO messages (sender_id, receiver_id, text, created_at) VALUES (?, ?, ?, ?)", (user_id, chat_id, text, local_time))
        else:
            db.execute("INSERT INTO messages (sender_id, group_id, text, created_at) VALUES (?, ?, ?, ?)", (user_id, chat_id, text, local_time))
    db.commit()
    return {"status": "ok"}

@chat_bp.route("/edit/<string:msg_id>", methods=["POST"])
def chat_edit(msg_id):
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]; db = get_db()
    
    msg = db.execute("SELECT sender_id, file_group_id, file_path, text FROM messages WHERE id=? OR file_group_id=?", (msg_id, msg_id)).fetchone()
    if not msg: return {"error": "Not found"}, 404
    if msg["sender_id"] != user_id: return {"error": "Forbidden"}, 403
    
    new_text = request.form.get("text", "").strip()
    local_time = (datetime.utcnow() + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    
    if msg["file_group_id"]:
        text_row = db.execute("SELECT id FROM messages WHERE file_group_id = ? AND text != '' LIMIT 1", (msg["file_group_id"],)).fetchone()
        if text_row: db.execute("UPDATE messages SET text = ? WHERE id = ?", (new_text, text_row["id"]))
        elif new_text:
            first_msg = db.execute("SELECT receiver_id, group_id FROM messages WHERE file_group_id = ? LIMIT 1", (msg["file_group_id"],)).fetchone()
            if first_msg:
                db.execute("INSERT INTO messages (sender_id, receiver_id, group_id, text, created_at, file_group_id) VALUES (?, ?, ?, ?, ?, ?)", (msg["sender_id"], first_msg["receiver_id"], first_msg["group_id"], new_text, local_time, msg["file_group_id"]))
    else:
        db.execute("UPDATE messages SET text = ? WHERE id = ?", (new_text, msg_id))
    
    files_to_delete = request.form.getlist("delete_file_ids")
    if files_to_delete:
        for f_id in files_to_delete:
            try:
                f_id = int(f_id)
                file_row = db.execute("SELECT id, file_path, text FROM messages WHERE id=?", (f_id,)).fetchone()
                if file_row and file_row["file_path"]:
                    file_to_delete = os.path.join(CHAT_UPLOAD_DIR, file_row["file_path"])
                    if os.path.exists(file_to_delete):
                        try: os.remove(file_to_delete)
                        except OSError: pass
                    if file_row["text"]: db.execute("UPDATE messages SET file_path = NULL, file_original_name = NULL WHERE id=?", (f_id,))
                    else: db.execute("DELETE FROM messages WHERE id=?", (f_id,))
            except ValueError: pass
    db.commit()
    return {"status": "ok"}

@chat_bp.route("/delete/<int:msg_id>", methods=["POST"])
def chat_delete(msg_id):
    if not session.get("chat_user_id"): return {"error": "Unauthorized"}, 401
    user_id = session["chat_user_id"]; db = get_db()
    msg = db.execute("SELECT sender_id, file_path, file_group_id FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg: return {"error": "Not found"}, 404
    if msg["sender_id"] != user_id: return {"error": "Forbidden"}, 403
    
    if msg["file_group_id"]:
        group_messages = db.execute("SELECT id, file_path FROM messages WHERE file_group_id = ?", (msg["file_group_id"],)).fetchall()
        for gm in group_messages:
            if gm["file_path"]:
                file_to_delete = os.path.join(CHAT_UPLOAD_DIR, gm["file_path"])
                if os.path.exists(file_to_delete):
                    try: os.remove(file_to_delete)
                    except OSError: pass
            db.execute("DELETE FROM messages WHERE id=?", (gm["id"],))
    else:
        if msg["file_path"]:
            file_to_delete = os.path.join(CHAT_UPLOAD_DIR, msg["file_path"])
            if os.path.exists(file_to_delete):
                try: os.remove(file_to_delete)
                except OSError: pass
        db.execute("DELETE FROM messages WHERE id=?", (msg_id,))
    db.commit()
    return {"status": "ok"}

@chat_bp.route("/download/<int:msg_id>")
def chat_download(msg_id):
    if not session.get("chat_user_id"): return redirect(url_for("auth.login"))
    db = get_db()
    msg = db.execute("SELECT file_path, file_original_name FROM messages WHERE id=?", (msg_id,)).fetchone()
    if msg and msg["file_path"]:
        return send_from_directory(CHAT_UPLOAD_DIR, msg["file_path"], as_attachment=True, download_name=msg["file_original_name"])
    abort(404)