import os, sqlite3, uuid, re, zipfile, tempfile, smtplib
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_from_directory, abort, Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from openpyxl import Workbook, load_workbook
from io import BytesIO
from config import UPLOAD_DIR, BASE_DIR, ALLOWED_EXT
from utils import get_db, log_action, login_required, current_user

main_bp = Blueprint('main', __name__)

# ================== СТАРТОВАЯ СТРАНИЦА (ХАБ) ==================
@main_bp.route("/")
def portal_home():
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    announcements = db.execute("""
        SELECT * FROM announcements 
        WHERE valid_until IS NULL OR valid_until = '' OR REPLACE(valid_until, 'T', ' ') >= ?
        ORDER BY id DESC
    """, (now,)).fetchall()
    
    processed_announcements = []
    url_regex = re.compile(r'(https?://[^\s<]+)') 
    current_host = request.host_url.rstrip("/")
    
    for ann in announcements:
        ann_dict = dict(ann)
        if ann_dict.get('content'):
            content = ann_dict['content']
            def replace_url(match):
                url = match.group(0)
                if current_host in url:
                    clean_url = url.replace(current_host, "")
                    return f'<a href="{clean_url}" target="_blank" rel="noopener noreferrer" class="text-decoration-none">{url}</a>'
                return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
            
            ann_dict['content'] = url_regex.sub(replace_url, content)
        processed_announcements.append(ann_dict)
        
    return render_template("portal.html", announcements=processed_announcements)

# ================== ПАНЕЛЬ АДМИНИСТРАТОРА ==================
@main_bp.route("/admin")
@login_required()
def admin_home():
    return render_template("admin_portal.html")

@main_bp.route("/admin/stats")
@login_required(roles=("admin",))
def admin_stats():
    db = get_db()
    total_docs = db.execute("SELECT COUNT(*) FROM documents WHERE is_deleted=0").fetchone()[0]
    actual_docs = db.execute("SELECT COUNT(*) FROM documents WHERE is_deleted=0 AND status='actual'").fetchone()[0]
    archived_docs = db.execute("SELECT COUNT(*) FROM documents WHERE is_deleted=0 AND status='archived'").fetchone()[0]
    total_employees = db.execute("SELECT COUNT(*) FROM employees WHERE is_deleted=0").fetchone()[0]
    yesterday = (datetime.utcnow() + timedelta(hours=5) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    total_messages = db.execute("SELECT COUNT(*) FROM messages WHERE created_at >= ?", (yesterday,)).fetchone()[0]
    top_users = db.execute("""
        SELECT e.full_name, COUNT(m.id) as msg_count
        FROM messages m
        JOIN employees e ON m.sender_id = e.id
        GROUP BY m.sender_id
        ORDER BY msg_count DESC
        LIMIT 5
    """).fetchall()
    doc_types = db.execute("""
        SELECT t.name, COUNT(d.id) as doc_count
        FROM doc_types t
        LEFT JOIN documents d ON t.id = d.doc_type_id AND d.is_deleted = 0
        GROUP BY t.id
        ORDER BY doc_count DESC
    """).fetchall()
    return render_template("admin_stats.html", 
                           total_docs=total_docs, actual_docs=actual_docs, archived_docs=archived_docs,
                           total_employees=total_employees, total_messages=total_messages,
                           top_users=top_users, doc_types=doc_types)

@main_bp.route("/admin/backup")
@login_required(roles=("admin",))
def admin_backup():
    now = (datetime.utcnow() + timedelta(hours=5)).strftime("%Y.%m.%d-%H.%M")
    filename = f"docs_portal_{now}.zip"
    
    temp_fd, temp_path = tempfile.mkstemp(suffix='.zip')
    os.close(temp_fd)
    
    try:
        with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Проходим по всем файлам БЕЗ исключений (включая venv и uploads)
            for root, dirs, files in os.walk(BASE_DIR):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Пропускаем только сам создаваемый файл бэкапа, чтобы он не попал в архив
                    if file.startswith('docs_portal_') and file.endswith('.zip'):
                        continue
                    
                    arcname = os.path.relpath(file_path, BASE_DIR)
                    zf.write(file_path, arcname)
        
        return send_file(temp_path, mimetype='application/zip', as_attachment=True, download_name=filename)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

@main_bp.route("/admin/help")
@login_required()
def admin_help():
    return render_template("help.html")

# ================== ДОКУМЕНТЫ ==================
@main_bp.route("/documents")
def documents_list():
    db = get_db()
    q = request.args.get("q", "").strip()
    dtype = request.args.get("type", "")
    direction = request.args.get("direction", "")
    status = request.args.get("status", "actual")

    sql = """SELECT d.*, t.name AS type_name,
                    (SELECT title FROM documents WHERE id=d.replaced_by_id) AS replaced_title,
                    (SELECT id FROM documents WHERE id=d.replaced_by_id) AS replaced_id
             FROM documents d JOIN doc_types t ON t.id = d.doc_type_id
             WHERE d.is_deleted = 0"""
    args = []
    if status in ("actual", "archived"):
        sql += " AND d.status = ?"; args.append(status)
    if q:
        sql += " AND (d.title LIKE ? OR d.number LIKE ? OR d.date_signed LIKE ? OR d.author LIKE ?)"
        like = f"%{q}%"; args += [like, like, like, like]
    if dtype:
        sql += " AND d.doc_type_id = ?"; args.append(dtype)
    if direction:
        sql += " AND d.direction = ?"; args.append(direction)
    sql += " ORDER BY d.date_signed DESC, d.id DESC"

    docs = db.execute(sql, args).fetchall()
    types = db.execute("SELECT * FROM doc_types ORDER BY sort_order, name").fetchall()
    directions = db.execute("SELECT * FROM directions ORDER BY id").fetchall()
    
    return render_template("index.html", docs=docs, types=types, directions=directions,
                           q=q, dtype=dtype, direction=direction, status=status)

@main_bp.route("/document/<int:doc_id>")
def document(doc_id):
    db = get_db()
    doc = db.execute("""SELECT d.*, t.name AS type_name FROM documents d
                        JOIN doc_types t ON t.id=d.doc_type_id WHERE d.id=? AND d.is_deleted=0""", (doc_id,)).fetchone()
    if not doc: abort(404)
    replaced = None
    if doc["replaced_by_id"]:
        replaced = db.execute("SELECT id,title,number FROM documents WHERE id=?", (doc["replaced_by_id"],)).fetchone()
    previous = db.execute("SELECT id,title,number,version,uploaded_at FROM documents WHERE parent_id=? ORDER BY version DESC", (doc_id,)).fetchall()
    return render_template("document.html", doc=doc, replaced=replaced, previous=previous)

@main_bp.route("/view/<int:doc_id>")
def view_pdf(doc_id):
    db = get_db()
    doc = db.execute("SELECT file_path FROM documents WHERE id=? AND is_deleted=0", (doc_id,)).fetchone()
    if not doc: abort(404)
    return send_from_directory(UPLOAD_DIR, doc["file_path"], mimetype="application/pdf")

@main_bp.route("/download/<int:doc_id>")
def download(doc_id):
    db = get_db()
    doc = db.execute("SELECT file_path, file_original_name FROM documents WHERE id=? AND is_deleted=0", (doc_id,)).fetchone()
    if not doc: abort(404)
    return send_from_directory(UPLOAD_DIR, doc["file_path"], as_attachment=True, download_name=doc["file_original_name"] or "document.pdf")

# ================== СОТРУДНИКИ ==================
@main_bp.route("/employees")
def employees_list():
    db = get_db()
    q = request.args.get("q", "").strip().lower()
    sql = """SELECT e.*, d.name AS dept_name FROM employees e
             LEFT JOIN departments d ON e.department_id = d.id
             WHERE e.is_deleted = 0
             ORDER BY d.name, e.full_name"""
    emps = db.execute(sql).fetchall()
    if q:
        filtered_emps = []
        for e in emps:
            if (q in (e["full_name"] or "").lower() or q in (e["position"] or "").lower() or 
                q in (e["dept_name"] or "").lower() or q in (e["email"] or "").lower() or 
                q in (e["phone"] or "").lower()):
                filtered_emps.append(e)
        emps = filtered_emps
    grouped = {}
    for e in emps:
        dept = e["dept_name"] if e["dept_name"] else "Без подразделения"
        grouped.setdefault(dept, []).append(e)
    return render_template("employees.html", grouped=grouped, q=q)

# ================== АДМИНКА: ДОКУМЕНТЫ ==================
@main_bp.route("/admin/documents")
@login_required()
def admin_documents():
    db = get_db()
    docs = db.execute("""SELECT d.*, t.name AS type_name FROM documents d
                         JOIN doc_types t ON t.id=d.doc_type_id
                         WHERE d.is_deleted = 0 ORDER BY d.uploaded_at DESC""").fetchall()
    return render_template("admin_documents.html", docs=docs)

def _save_pdf(fs):
    if not fs or fs.filename == "": return None, None
    ext = fs.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT: raise ValueError("Разрешён только PDF")
    orig = secure_filename(fs.filename)
    stored = f"{uuid.uuid4().hex}.pdf"
    fs.save(os.path.join(UPLOAD_DIR, stored))
    return stored, orig

@main_bp.route("/admin/documents/new", methods=["GET", "POST"])
@login_required()
def admin_new():
    db = get_db()
    types = db.execute("SELECT * FROM doc_types ORDER BY sort_order, name").fetchall()
    directions = db.execute("SELECT * FROM directions ORDER BY id").fetchall()
    actual_docs = db.execute("SELECT id,title,number FROM documents WHERE status='actual' AND is_deleted=0 ORDER BY title").fetchall()
    if request.method == "POST":
        f = request.form
        try:
            stored, orig = _save_pdf(request.files.get("file"))
            if not stored: raise ValueError("Прикрепите PDF-файл")
        except ValueError as e:
            flash(str(e), "danger"); return redirect(request.url)
        replaced_by = f.get("replaced_by_id") or None
        cur = db.execute("""INSERT INTO documents (title,number,doc_type_id,direction,author,date_signed,description,status,replaced_by_id,version,parent_id,file_path,file_original_name,uploaded_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f["title"].strip(), f.get("number","").strip(), f["doc_type_id"], f.get("direction","general"), f.get("author","").strip(), f.get("date_signed") or None, f.get("description","").strip(), "actual", replaced_by, 1, None, stored, orig, session["username"]))
        doc_id = cur.lastrowid
        if replaced_by:
            db.execute("UPDATE documents SET status='archived', replaced_by_id=? WHERE id=?", (doc_id, replaced_by))
        db.commit(); log_action("Создан документ", doc_id, f["title"])
        flash("Документ добавлен", "success"); return redirect(url_for("main.admin_documents"))
    return render_template("admin_form.html", doc=None, types=types, directions=directions, actual_docs=actual_docs)

@main_bp.route("/admin/documents/<int:doc_id>/edit", methods=["GET", "POST"])
@login_required()
def admin_edit(doc_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id=? AND is_deleted=0", (doc_id,)).fetchone()
    if not doc: abort(404)
    types = db.execute("SELECT * FROM doc_types ORDER BY sort_order, name").fetchall()
    directions = db.execute("SELECT * FROM directions ORDER BY id").fetchall()
    actual_docs = db.execute("SELECT id,title,number FROM documents WHERE status='actual' AND id<>? AND is_deleted=0 ORDER BY title", (doc_id,)).fetchall()
    if request.method == "POST":
        f = request.form
        file_path = doc["file_path"]; file_original_name = doc["file_original_name"]
        if request.files.get("file") and request.files["file"].filename != "":
            try:
                new_stored, new_orig = _save_pdf(request.files["file"])
                if new_stored:
                    file_path = new_stored; file_original_name = new_orig
                    try: os.remove(os.path.join(UPLOAD_DIR, doc["file_path"]))
                    except OSError: pass
            except ValueError as e:
                flash(str(e), "danger"); return redirect(request.url)
        db.execute("""UPDATE documents SET title=?, number=?, doc_type_id=?, direction=?, author=?, date_signed=?, description=?, status=?, replaced_by_id=?, file_path=?, file_original_name=? WHERE id=?""",
                   (f["title"].strip(), f.get("number","").strip(), f["doc_type_id"], f.get("direction","general"), f.get("author","").strip(), f.get("date_signed") or None, f.get("description","").strip(), f.get("status","actual"), f.get("replaced_by_id") or None, file_path, file_original_name, doc_id))
        db.commit(); log_action("Изменён документ", doc_id, f["title"])
        flash("Изменения сохранены", "success"); return redirect(url_for("main.admin_documents"))
    return render_template("admin_form.html", doc=doc, types=types, directions=directions, actual_docs=actual_docs)

@main_bp.route("/admin/documents/<int:doc_id>/new_version", methods=["GET", "POST"])
@login_required()
def admin_new_version(doc_id):
    db = get_db()
    old = db.execute("SELECT * FROM documents WHERE id=? AND is_deleted=0", (doc_id,)).fetchone()
    if not old: abort(404)
    types = db.execute("SELECT * FROM doc_types ORDER BY sort_order, name").fetchall()
    directions = db.execute("SELECT * FROM directions ORDER BY id").fetchall()
    if request.method == "POST":
        f = request.form
        try:
            stored, orig = _save_pdf(request.files.get("file"))
            if not stored: raise ValueError("Прикрепите PDF новой редакции")
        except ValueError as e:
            flash(str(e), "danger"); return redirect(request.url)
        cur = db.execute("""INSERT INTO documents (title,number,doc_type_id,direction,author,date_signed,description,status,replaced_by_id,version,parent_id,file_path,file_original_name,uploaded_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f["title"].strip(), f.get("number","").strip(), f["doc_type_id"], f.get("direction","general"), f.get("author","").strip(), f.get("date_signed") or None, f.get("description","").strip(), "actual", None, old["version"] + 1, old["id"], stored, orig, session["username"]))
        new_id = cur.lastrowid
        db.execute("UPDATE documents SET status='archived', replaced_by_id=? WHERE id=?", (new_id, doc_id))
        db.execute("UPDATE documents SET replaced_by_id=? WHERE replaced_by_id=?", (new_id, doc_id))
        db.commit(); log_action("Создана новая редакция", new_id, f"Версия {old['version']+1} для «{old['title']}»")
        flash("Новая редакция создана", "success"); return redirect(url_for("main.admin_documents"))
    return render_template("admin_form.html", doc=old, types=types, directions=directions, actual_docs=[], is_new_version=True)

@main_bp.route("/admin/documents/<int:doc_id>/archive", methods=["POST"])
@login_required()
def admin_archive(doc_id):
    db = get_db()
    db.execute("UPDATE documents SET status='archived' WHERE id=?", (doc_id,))
    db.commit(); log_action("Документ переведён в неактуальные", doc_id)
    return redirect(url_for("main.admin_documents"))

@main_bp.route("/admin/documents/<int:doc_id>/restore", methods=["POST"])
@login_required()
def admin_restore(doc_id):
    db = get_db()
    db.execute("UPDATE documents SET status='actual', replaced_by_id=NULL WHERE id=?", (doc_id,))
    db.commit(); log_action("Документ возвращён в актуальные", doc_id)
    return redirect(url_for("main.admin_documents"))

@main_bp.route("/admin/documents/<int:doc_id>/delete", methods=["POST"])
@login_required(roles=("admin",))
def admin_delete(doc_id):
    db = get_db()
    row = db.execute("SELECT title FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row:
        db.execute("UPDATE documents SET is_deleted=1 WHERE id=?", (doc_id,))
        db.commit(); log_action("Документ перемещён в корзину", doc_id, row["title"])
        flash("Документ перемещён в корзину", "success")
    return redirect(url_for("main.admin_documents"))

# ================== АДМИНКА: КОРЗИНА ДОКУМЕНТОВ ==================
@main_bp.route("/admin/trash/documents")
@login_required(roles=("admin",))
def admin_trash_documents():
    db = get_db()
    docs = db.execute("""SELECT d.*, t.name AS type_name FROM documents d
                         JOIN doc_types t ON t.id=d.doc_type_id
                         WHERE d.is_deleted = 1 ORDER BY d.uploaded_at DESC""").fetchall()
    return render_template("admin_trash_documents.html", docs=docs)

@main_bp.route("/admin/documents/<int:doc_id>/restore_from_trash", methods=["POST"])
@login_required(roles=("admin",))
def admin_restore_doc_from_trash(doc_id):
    db = get_db()
    db.execute("UPDATE documents SET is_deleted=0 WHERE id=?", (doc_id,))
    db.commit(); log_action("Документ восстановлен из корзины", doc_id)
    flash("Документ восстановлен", "success")
    return redirect(url_for("main.admin_trash_documents"))

@main_bp.route("/admin/documents/<int:doc_id>/delete_forever", methods=["POST"])
@login_required(roles=("admin",))
def admin_delete_doc_forever(doc_id):
    db = get_db()
    row = db.execute("SELECT file_path,title FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row:
        db.execute("UPDATE documents SET replaced_by_id=NULL WHERE replaced_by_id=?", (doc_id,))
        db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        db.commit()
        try: os.remove(os.path.join(UPLOAD_DIR, row["file_path"]))
        except OSError: pass
        log_action("Документ удалён навсегда", doc_id, row["title"])
        flash("Документ удалён навсегда", "danger")
    return redirect(url_for("main.admin_trash_documents"))

# ================== АДМИНКА: СОТРУДНИКИ ==================
@main_bp.route("/admin/employees")
@login_required()
def admin_employees():
    db = get_db()
    q = request.args.get("q", "").strip().lower()
    sql = """SELECT e.*, d.name AS dept_name FROM employees e
             LEFT JOIN departments d ON e.department_id = d.id
             WHERE e.is_deleted = 0 ORDER BY d.name, e.full_name"""
    emps = db.execute(sql).fetchall()
    if q:
        filtered_emps = []
        for e in emps:
            if (q in (e["full_name"] or "").lower() or q in (e["position"] or "").lower() or 
                q in (e["dept_name"] or "").lower() or q in (e["email"] or "").lower() or 
                q in (e["phone"] or "").lower()):
                filtered_emps.append(e)
        emps = filtered_emps
    return render_template("admin_employees.html", emps=emps, q=q)

@main_bp.route("/admin/employees/import", methods=["POST"])
@login_required()
def admin_employees_import():
    f = request.files.get("file")
    if not f or f.filename == "":
        flash("Выберите Excel-файл", "danger"); return redirect(url_for("main.admin_employees"))
    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("xlsx", "xls"):
        flash("Нужен файл Excel (.xlsx)", "danger"); return redirect(url_for("main.admin_employees"))
    try:
        wb = load_workbook(f, data_only=True)
        ws = wb.active
        db = get_db()
        db.execute("DELETE FROM employees")
        count = 0; current_dept = "Без подразделения"
        for row in ws.iter_rows(min_row=1, values_only=True):
            if not row or all(cell is None for cell in row): continue
            col_a = str(row[0]).strip() if row[0] else ""
            col_b = str(row[1]).strip() if len(row) > 1 and row[1] else ""
            col_c = str(row[2]).strip() if len(row) > 2 and row[2] else ""
            col_d = str(row[3]).strip() if len(row) > 3 and row[3] else ""
            col_e = str(row[4]).strip() if len(row) > 4 and row[4] else ""
            col_f = str(row[5]).strip() if len(row) > 5 and row[5] else ""
            if "Контактная информация" in col_a or "Дата формирования" in col_a or "Сотрудник" in col_a: continue
            if not col_b and not col_c and not col_d and not col_f:
                current_dept = col_a; continue
            if col_a and col_c:
                full_name = col_a; position = col_c
                email = col_d if col_d else col_e
                phone = col_f if col_f else (col_e if "@" not in col_e else "")
                if not email and "@" not in col_e: phone = col_e
                dept_row = db.execute("SELECT id FROM departments WHERE name=?", (current_dept,)).fetchone()
                if dept_row: dept_id = dept_row["id"]
                else:
                    cur_dept = db.execute("INSERT INTO departments (name) VALUES (?)", (current_dept,))
                    dept_id = cur_dept.lastrowid
                db.execute("""INSERT INTO employees (full_name, position, department_id, email, phone) VALUES (?,?,?,?,?)""", (full_name, position, dept_id, email, phone))
                count += 1
        db.commit(); log_action("Импорт сотрудников из Excel", details=f"Добавлено: {count}")
        flash(f"Успешно импортировано сотрудников: {count}", "success")
    except Exception as e:
        flash(f"Ошибка чтения файла: {e}", "danger")
    return redirect(url_for("main.admin_employees"))

@main_bp.route("/admin/employees/new", methods=["GET", "POST"])
@login_required()
def admin_employee_new():
    db = get_db()
    departments = db.execute("SELECT * FROM departments ORDER BY name").fetchall()
    if request.method == "POST":
        f = request.form
        dept_id = f.get("department_id")
        if not dept_id: dept_id = None
        db.execute("""INSERT INTO employees (full_name, position, department_id, email, phone) VALUES (?,?,?,?,?)""",
                   (f["full_name"].strip(), f.get("position","").strip(), dept_id, f.get("email","").strip(), f.get("phone","").strip()))
        db.commit(); log_action("Добавлен сотрудник", details=f["full_name"])
        flash("Сотрудник добавлен", "success"); return redirect(url_for("main.admin_employees"))
    return render_template("admin_employee_form.html", emp=None, departments=departments)

@main_bp.route("/admin/employees/<int:emp_id>/edit", methods=["GET", "POST"])
@login_required()
def admin_employee_edit(emp_id):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=? AND is_deleted=0", (emp_id,)).fetchone()
    if not emp: abort(404)
    departments = db.execute("SELECT * FROM departments ORDER BY name").fetchall()
    if request.method == "POST":
        f = request.form
        dept_id = f.get("department_id")
        if not dept_id: dept_id = None
        db.execute("""UPDATE employees SET full_name=?, position=?, department_id=?, email=?, phone=? WHERE id=?""",
                   (f["full_name"].strip(), f.get("position","").strip(), dept_id, f.get("email","").strip(), f.get("phone","").strip(), emp_id))
        db.commit(); log_action("Изменён сотрудник", details=f["full_name"])
        flash("Данные сотрудника обновлены", "success"); return redirect(url_for("main.admin_employees"))
    return render_template("admin_employee_form.html", emp=emp, departments=departments)

@main_bp.route("/admin/employees/<int:emp_id>/delete", methods=["POST"])
@login_required(roles=("admin",))
def admin_employee_delete(emp_id):
    db = get_db()
    emp = db.execute("SELECT full_name FROM employees WHERE id=?", (emp_id,)).fetchone()
    if emp:
        db.execute("UPDATE employees SET is_deleted=1 WHERE id=?", (emp_id,))
        db.commit(); log_action("Сотрудник перемещён в корзину", details=emp["full_name"])
        flash("Сотрудник перемещён в корзину", "success")
    return redirect(url_for("main.admin_employees"))

# ================== АДМИНКА: КОРЗИНА СОТРУДНИКОВ ==================
@main_bp.route("/admin/trash/employees")
@login_required(roles=("admin",))
def admin_trash_employees():
    db = get_db()
    emps = db.execute("""SELECT e.*, d.name AS dept_name FROM employees e
                         LEFT JOIN departments d ON e.department_id = d.id
                         WHERE e.is_deleted = 1 ORDER BY d.name, e.full_name""").fetchall()
    return render_template("admin_trash_employees.html", emps=emps)

@main_bp.route("/admin/employees/<int:emp_id>/restore_from_trash", methods=["POST"])
@login_required(roles=("admin",))
def admin_restore_emp_from_trash(emp_id):
    db = get_db()
    db.execute("UPDATE employees SET is_deleted=0 WHERE id=?", (emp_id,))
    db.commit(); log_action("Сотрудник восстановлен из корзины", details=str(emp_id))
    flash("Сотрудник восстановлен", "success"); return redirect(url_for("main.admin_trash_employees"))

@main_bp.route("/admin/employees/<int:emp_id>/delete_forever", methods=["POST"])
@login_required(roles=("admin",))
def admin_delete_emp_forever(emp_id):
    db = get_db()
    emp = db.execute("SELECT full_name FROM employees WHERE id=?", (emp_id,)).fetchone()
    if emp:
        db.execute("DELETE FROM employees WHERE id=?", (emp_id,))
        db.commit(); log_action("Сотрудник удалён навсегда", details=emp["full_name"])
        flash("Сотрудник удалён навсегда", "danger")
    return redirect(url_for("main.admin_trash_employees"))

# ================== АДМИНКА: ОТДЕЛЫ ==================
@main_bp.route("/admin/departments", methods=["GET", "POST"])
@login_required()
def admin_departments():
    db = get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            try:
                db.execute("INSERT INTO departments (name) VALUES (?)", (name,))
                db.commit(); log_action("Добавлен отдел", details=name)
                flash("Отдел добавлен", "success")
            except sqlite3.IntegrityError: flash("Такой отдел уже есть", "warning")
        return redirect(url_for("main.admin_departments"))
    depts = db.execute("""SELECT d.*, (SELECT COUNT(*) FROM employees WHERE department_id=d.id AND is_deleted=0) AS cnt
                          FROM departments d ORDER BY d.name""").fetchall()
    return render_template("admin_departments.html", depts=depts)

@main_bp.route("/admin/departments/<int:dept_id>/edit", methods=["GET", "POST"])
@login_required()
def admin_department_edit(dept_id):
    db = get_db()
    dept = db.execute("SELECT * FROM departments WHERE id=?", (dept_id,)).fetchone()
    if not dept: abort(404)
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            try:
                db.execute("UPDATE departments SET name=? WHERE id=?", (name, dept_id))
                db.commit(); log_action("Переименован отдел", details=f"{dept['name']} -> {name}")
                flash("Название отдела изменено", "success")
            except sqlite3.IntegrityError: flash("Отдел с таким названием уже существует", "warning")
        return redirect(url_for("main.admin_departments"))
    return render_template("admin_department_form.html", dept=dept)

@main_bp.route("/admin/departments/<int:dept_id>/delete", methods=["POST"])
@login_required(roles=("admin",))
def admin_department_delete(dept_id):
    db = get_db()
    cnt = db.execute("SELECT COUNT(*) FROM employees WHERE department_id=? AND is_deleted=0", (dept_id,)).fetchone()[0]
    if cnt: flash("Нельзя удалить отдел — к нему привязаны сотрудники", "warning")
    else:
        db.execute("DELETE FROM departments WHERE id=?", (dept_id,))
        db.commit(); log_action("Удалён отдел", details=str(dept_id))
        flash("Отдел удален", "success")
    return redirect(url_for("main.admin_departments"))

# ================== АДМИНКА: РАССЫЛКА ==================
@main_bp.route("/admin/employees/mailing", methods=["GET", "POST"])
@login_required()
def admin_mailing():
    db = get_db()
    settings = {}
    for row in db.execute("SELECT key, value FROM settings").fetchall():
        settings[row["key"]] = row["value"]
    employees = db.execute("""SELECT id, full_name, position, email, department_id 
                              FROM employees WHERE is_deleted = 0 AND email != '' AND email IS NOT NULL ORDER BY full_name""").fetchall()
    if request.method == "POST":
        f = request.form
        subject = f.get("subject", "").strip()
        body = f.get("body", "").strip()
        recipients = f.getlist("recipients")
        attachments = request.files.getlist("attachments")
        if not subject or not body:
            flash("Заполните тему и текст письма", "danger"); return redirect(request.url)
        if not recipients:
            flash("Выберите хотя бы одного получателя", "warning"); return redirect(request.url)
        placeholders = ','.join('?' * len(recipients))
        emails = db.execute(f"SELECT email FROM employees WHERE id IN ({placeholders})", recipients).fetchall()
        try:
            server = smtplib.SMTP_SSL(settings.get("smtp_server"), int(settings.get("smtp_port")))
            server.login(settings.get("smtp_user"), settings.get("smtp_password"))
            count = 0
            for row in emails:
                msg = MIMEMultipart()
                msg['From'] = settings.get("smtp_user")
                msg['To'] = row["email"]
                msg['Subject'] = subject
                if attachments:
                    from email.mime.base import MIMEBase
                    from email import encoders
                    from email.header import Header
                    for att in attachments:
                        if att and att.filename:
                            part = MIMEBase('application', 'octet-stream')
                            part.set_payload(att.read())
                            encoders.encode_base64(part)
                            part.add_header('Content-Disposition', 'attachment', filename=Header(att.filename, 'utf-8').encode())
                            msg.attach(part)                
                server.send_message(msg); count += 1
            server.quit()
            log_action("Рассылка сотрудникам", details=f"Отправлено {count} писем. Тема: {subject}")
            flash(f"Успешно отправлено {count} писем!", "success"); return redirect(url_for("main.admin_employees"))
        except Exception as e:
            flash(f"Ошибка отправки: {e}", "danger")
    departments = db.execute("SELECT * FROM departments ORDER BY name").fetchall()
    return render_template("admin_mailing.html", employees=employees, departments=departments)

# ================== АДМИНКА: ОБЪЯВЛЕНИЯ ==================
@main_bp.route("/admin/announcements")
@login_required()
def admin_announcements():
    db = get_db()
    rows = db.execute("SELECT * FROM announcements ORDER BY id DESC").fetchall()
    return render_template("admin_announcements.html", announcements=rows)

@main_bp.route("/admin/announcements/new", methods=["GET", "POST"])
@login_required()
def admin_announcement_new():
    if request.method == "POST":
        f = request.form; db = get_db()
        local_time = (datetime.utcnow() + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("""INSERT INTO announcements (title, content, valid_until, created_by, created_at) VALUES (?,?,?,?,?)""",
                   (f["title"].strip(), f["content"].strip(), f.get("valid_until") or None, session["username"], local_time))
        db.commit(); log_action("Создано объявление", details=f["title"])
        flash("Объявление создано", "success"); return redirect(url_for("main.admin_announcements"))
    return render_template("admin_announcement_form.html", ann=None)

@main_bp.route("/admin/announcements/<int:ann_id>/edit", methods=["GET", "POST"])
@login_required()
def admin_announcement_edit(ann_id):
    db = get_db()
    ann = db.execute("SELECT * FROM announcements WHERE id=?", (ann_id,)).fetchone()
    if not ann: abort(404)
    if request.method == "POST":
        f = request.form
        db.execute("""UPDATE announcements SET title=?, content=?, valid_until=? WHERE id=?""",
                   (f["title"].strip(), f["content"].strip(), f.get("valid_until") or None, ann_id))
        db.commit(); log_action("Изменено объявление", details=f["title"])
        flash("Объявление обновлено", "success"); return redirect(url_for("main.admin_announcements"))
    return render_template("admin_announcement_form.html", ann=ann)

@main_bp.route("/admin/announcements/<int:ann_id>/delete", methods=["POST"])
@login_required(roles=("admin",))
def admin_announcement_delete(ann_id):
    db = get_db()
    ann = db.execute("SELECT title FROM announcements WHERE id=?", (ann_id,)).fetchone()
    if ann:
        db.execute("DELETE FROM announcements WHERE id=?", (ann_id,))
        db.commit(); log_action("Удалено объявление", details=ann["title"])
        flash("Объявление удалено", "success")
    return redirect(url_for("main.admin_announcements"))

# ================== АДМИНКА: НАПРАВЛЕНИЯ ==================
@main_bp.route("/admin/directions", methods=["GET", "POST"])
@login_required()
def admin_directions():
    db = get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            import re
            code = re.sub(r'[^a-z0-9]', '', name.lower())
            if not code:
                import uuid
                code = uuid.uuid4().hex[:8]
            try:
                db.execute("INSERT INTO directions (code, name) VALUES (?, ?)", (code, name))
                db.commit(); log_action("Добавлено направление", details=name)
                flash("Направление добавлено", "success")
            except sqlite3.IntegrityError: flash("Направление с таким названием уже есть", "warning")
        return redirect(url_for("main.admin_directions"))
    dirs = db.execute("""SELECT d.*, (SELECT COUNT(*) FROM documents WHERE direction=d.code AND is_deleted=0) AS cnt
                         FROM directions d ORDER BY d.id""").fetchall()
    return render_template("admin_directions.html", dirs=dirs)

@main_bp.route("/admin/directions/<int:dir_id>/edit", methods=["GET", "POST"])
@login_required()
def admin_direction_edit(dir_id):
    db = get_db()
    direction = db.execute("SELECT * FROM directions WHERE id=?", (dir_id,)).fetchone()
    if not direction: abort(404)
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            db.execute("UPDATE directions SET name=? WHERE id=?", (name, dir_id))
            db.commit(); log_action("Изменено направление", details=f"{direction['name']} -> {name}")
            flash("Название направления изменено", "success")
            return redirect(url_for("main.admin_directions"))
    return render_template("admin_direction_form.html", direction=direction)

@main_bp.route("/admin/directions/<int:dir_id>/delete", methods=["POST"])
@login_required(roles=("admin",))
def admin_direction_delete(dir_id):
    db = get_db()
    direction = db.execute("SELECT * FROM directions WHERE id=?", (dir_id,)).fetchone()
    if not direction: abort(404)
    cnt = db.execute("SELECT COUNT(*) FROM documents WHERE direction=? AND is_deleted=0", (direction["code"],)).fetchone()[0]
    if cnt: flash("Нельзя удалить направление — к нему привязаны документы", "warning")
    else:
        db.execute("DELETE FROM directions WHERE id=?", (dir_id,))
        db.commit(); log_action("Удалено направление", details=direction["name"])
        flash("Направление удалено", "success")
    return redirect(url_for("main.admin_directions"))

# ================== ПРОЧЕЕ ==================
@main_bp.route("/admin/settings/mail", methods=["GET", "POST"])
@login_required(roles=("admin",))
def admin_mail_settings():
    db = get_db()
    if request.method == "POST":
        f = request.form
        try:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('smtp_server', ?)", (f["smtp_server"].strip(),))
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('smtp_port', ?)", (f["smtp_port"].strip(),))
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('smtp_user', ?)", (f["smtp_user"].strip(),))
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('smtp_sender_name', ?)", (f["smtp_sender_name"].strip(),))
            if f.get("smtp_password"):
                db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('smtp_password', ?)", (f["smtp_password"].strip(),))
            db.commit(); log_action("Изменены настройки почты")
            flash("Настройки почты сохранены", "success")
        except Exception as e: flash(f"Ошибка сохранения: {e}", "danger")
        return redirect(url_for("main.admin_mail_settings"))
    current = {}
    for row in db.execute("SELECT key, value FROM settings").fetchall(): current[row["key"]] = row["value"]
    return render_template("admin_mail_settings.html", settings=current)

@main_bp.route("/admin/types", methods=["GET", "POST"])
@login_required()
def admin_types():
    db = get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            try:
                db.execute("INSERT INTO doc_types(name) VALUES (?)", (name,))
                db.commit(); log_action("Добавлен тип документа", details=name)
            except sqlite3.IntegrityError: flash("Такой тип уже есть", "warning")
        return redirect(url_for("main.admin_types"))
    types = db.execute("""SELECT t.*, (SELECT COUNT(*) FROM documents WHERE doc_type_id=t.id AND is_deleted=0) AS cnt
                          FROM doc_types t ORDER BY sort_order, name""").fetchall()
    return render_template("admin_types.html", types=types)

@main_bp.route("/admin/types/<int:type_id>/delete", methods=["POST"])
@login_required(roles=("admin",))
def admin_type_delete(type_id):
    db = get_db()
    cnt = db.execute("SELECT COUNT(*) FROM documents WHERE doc_type_id=? AND is_deleted=0", (type_id,)).fetchone()[0]
    if cnt: flash("Нельзя удалить тип — к нему привязаны документы", "warning")
    else:
        db.execute("DELETE FROM doc_types WHERE id=?", (type_id,))
        db.commit(); log_action("Удалён тип документа", details=str(type_id))
    return redirect(url_for("main.admin_types"))

@main_bp.route("/admin/users", methods=["GET", "POST"])
@login_required(roles=("admin",))
def admin_users():
    db = get_db()
    if request.method == "POST":
        u = request.form["username"].strip(); p = request.form["password"]; r = request.form["role"]
        if r not in ("admin", "editor"): flash("Неверная роль", "danger")
        elif not u or not p: flash("Заполните логин и пароль", "danger")
        else:
            try:
                db.execute("INSERT INTO users(username,password_hash,role) VALUES (?,?,?)", (u, generate_password_hash(p), r))
                db.commit(); log_action("Создан пользователь", details=f"{u} ({r})")
                flash("Пользователь создан", "success")
            except sqlite3.IntegrityError: flash("Логин уже существует", "warning")
        return redirect(url_for("main.admin_users"))
    users = db.execute("SELECT id,username,role FROM users ORDER BY username").fetchall()
    return render_template("admin_users.html", users=users)

@main_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required(roles=("admin",))
def admin_user_delete(user_id):
    db = get_db()
    row = db.execute("SELECT username,role FROM users WHERE id=?", (user_id,)).fetchone()
    if row and row["username"] == session.get("username"): flash("Нельзя удалить самого себя", "warning")
    elif row:
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        db.commit(); log_action("Удалён пользователь", details=row["username"])
    return redirect(url_for("main.admin_users"))

@main_bp.route("/admin/users/<int:user_id>/reset", methods=["POST"])
@login_required(roles=("admin",))
def admin_user_reset(user_id):
    p = request.form.get("password","").strip()
    if not p: flash("Введите новый пароль", "danger")
    else:
        db = get_db()
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(p), user_id))
        db.commit(); log_action("Смена пароля пользователя", details=str(user_id))
        flash("Пароль изменён", "success")
    return redirect(url_for("main.admin_users"))

@main_bp.route("/admin/log")
@login_required(roles=("admin",))
def admin_log():
    db = get_db()
    rows = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 2000").fetchall()
    return render_template("admin_log.html", rows=rows)

# ================== ЭКСПОРТ ==================
@main_bp.route("/export.xlsx")
@login_required()
def export_xlsx():
    db = get_db()
    rows = db.execute("""SELECT d.id, d.title, d.number, t.name AS type, d.direction,
                                d.author, d.date_signed, d.status, d.version, d.uploaded_at
                         FROM documents d JOIN doc_types t ON t.id=d.doc_type_id
                         WHERE d.is_deleted=0 ORDER BY d.date_signed DESC""").fetchall()
    wb = Workbook(); ws = wb.active; ws.title = "Документы"
    ws.append(["ID","Наименование","Номер","Тип","Направление","Автор/подразделение","Дата","Статус","Версия","Загружен"])
    for r in rows:
        ws.append([r["id"], r["title"], r["number"], r["type"],
                   "Информационная безопасность" if r["direction"]=="infosec" else "Общее",
                   r["author"], r["date_signed"], "Действует" if r["status"]=="actual" else "Не действует",
                   r["version"], r["uploaded_at"]])
    bio = BytesIO(); wb.save(bio); bio.seek(0)
    return Response(bio.read(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":"attachment; filename=documents.xlsx"})

@main_bp.route("/export_employees.xlsx")
@login_required()
def export_employees():
    db = get_db()
    rows = db.execute("""SELECT e.full_name, e.position, d.name AS dept_name, e.email, e.phone
                         FROM employees e LEFT JOIN departments d ON e.department_id = d.id
                         WHERE e.is_deleted = 0 ORDER BY d.name, e.full_name""").fetchall()
    wb = Workbook(); ws = wb.active; ws.title = "Сотрудники"
    ws.append(["ФИО", "Должность", "Подразделение / Отдел", "Email", "Телефон"])
    for r in rows:
        ws.append([r["full_name"], r["position"], r["dept_name"], r["email"], r["phone"]])
    for col in ws.columns:
        max_length = 0; column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length: max_length = len(str(cell.value))
            except: pass
        ws.column_dimensions[column].width = (max_length + 2)
    bio = BytesIO(); wb.save(bio); bio.seek(0)
    log_action("Экспорт сотрудников в Excel")
    return Response(bio.read(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=employees.xlsx"})

# ================== УПРАВЛЕНИЕ МЕССЕНДЖЕРОМ (АДМИНКА) ==================
@main_bp.route("/admin/chat_settings", methods=["POST"])
@login_required(roles=("admin",))
def admin_chat_settings():
    db = get_db()
    f = request.form
    try:
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_keep_messages_days', ?)", (f.get("keep_messages", "14").strip(),))
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_keep_files_days', ?)", (f.get("keep_files", "7").strip(),))
        db.commit()
        flash("Настройки хранения чата сохранены", "success")
    except Exception as e:
        flash(f"Ошибка сохранения: {e}", "danger")
    return redirect(url_for("main.admin_chat_management"))

@main_bp.route("/admin/chat_emergency_clear", methods=["POST"])
@login_required(roles=("admin",))
def admin_chat_emergency_clear():
    password = request.form.get("password", "")
    u, _ = current_user()
    row = get_db().execute("SELECT password_hash FROM users WHERE username=?", (u,)).fetchone()
    
    if not row or not check_password_hash(row["password_hash"], password):
        flash("Неверный пароль администратора. Очистка отменена.", "danger")
        return redirect(url_for("main.admin_chat_management"))
    
    db = get_db()
    if os.path.exists(CHAT_UPLOAD_DIR):
        for file in os.listdir(CHAT_UPLOAD_DIR):
            file_path = os.path.join(CHAT_UPLOAD_DIR, file)
            if os.path.isfile(file_path):
                try: os.remove(file_path)
                except OSError: pass
                
    db.execute("DELETE FROM messages")
    db.execute("DELETE FROM chat_reads")
    db.commit()
    
    log_action("Экстренная очистка чата", details="Удалены все сообщения и файлы")
    flash("Все сообщения и файлы успешно удалены.", "warning")
    return redirect(url_for("main.admin_chat_management"))

@main_bp.route("/admin/chat_management", methods=["GET", "POST"])
@login_required(roles=("admin",))
def admin_chat_management():
    db = get_db()
    if request.method == "POST":
        emp_id = request.form.get("employee_id"); action = request.form.get("action")
        if action == "enable":
            password = request.form.get("password")
            if not password:
                flash("Введите пароль для сотрудника", "danger"); return redirect(url_for("main.admin_chat_management"))
            from werkzeug.security import generate_password_hash
            db.execute("UPDATE employees SET chat_enabled=1, chat_password_hash=? WHERE id=?", (generate_password_hash(password), emp_id))
            db.commit(); log_action("Включен доступ к мессенджеру", details=str(emp_id))
            flash("Доступ к мессенджеру включен и пароль установлен", "success")
        elif action == "disable":
            db.execute("UPDATE employees SET chat_enabled=0, chat_password_hash=NULL WHERE id=?", (emp_id,))
            db.commit(); log_action("Отключен доступ к мессенджеру", details=str(emp_id))
            flash("Доступ к мессенджеру отключен", "warning")
        elif action == "change_password":
            new_password = request.form.get("new_password")
            if not new_password:
                flash("Введите новый пароль", "danger")
            else:
                from werkzeug.security import generate_password_hash
                db.execute("UPDATE employees SET chat_password_hash=? WHERE id=?", (generate_password_hash(new_password), emp_id))
                db.commit(); log_action("Изменен пароль от мессенджера", details=str(emp_id))
                flash("Пароль успешно изменен", "success")
        return redirect(url_for("main.admin_chat_management"))
    employees = db.execute("""SELECT id, full_name, position, chat_enabled FROM employees WHERE is_deleted=0 ORDER BY chat_enabled DESC, full_name""").fetchall()
    
    settings = {}
    for row in db.execute("SELECT key, value FROM settings WHERE key LIKE 'chat_keep_%'").fetchall():
        settings[row["key"]] = row["value"]
        
    return render_template("admin_chat_management.html", employees=employees, settings=settings)

@main_bp.route("/admin/chat_groups", methods=["GET", "POST"])
@login_required(roles=("admin",))
def admin_chat_groups():
    db = get_db()
    if request.method == "POST":
        group_name = request.form.get("group_name", "").strip()
        if group_name:
            try:
                db.execute("INSERT INTO chat_groups (name) VALUES (?)", (group_name,))
                db.commit(); log_action("Создана группа чата", details=group_name)
                flash("Группа создана", "success")
            except sqlite3.IntegrityError: flash("Группа с таким названием уже существует", "warning")
        return redirect(url_for("main.admin_chat_groups"))
    groups = db.execute("""SELECT g.*, (SELECT COUNT(*) FROM group_members WHERE group_id=g.id) as members_count FROM chat_groups g ORDER BY g.name""").fetchall()
    return render_template("admin_chat_groups.html", groups=groups)

@main_bp.route("/admin/chat_groups/<int:group_id>/edit", methods=["GET", "POST"])
@login_required(roles=("admin",))
def admin_chat_group_edit(group_id):
    db = get_db()
    group = db.execute("SELECT * FROM chat_groups WHERE id=?", (group_id,)).fetchone()
    if not group: abort(404)
    if request.method == "POST":
        if "add_member" in request.form:
            emp_id = request.form.get("employee_id")
            if emp_id:
                try:
                    db.execute("INSERT INTO group_members (group_id, employee_id) VALUES (?, ?)", (group_id, emp_id))
                    db.commit(); log_action("Добавлен в группу", details=f"Группа {group['name']}, Сотрудник ID {emp_id}")
                    flash("Сотрудник добавлен в группу", "success")
                except sqlite3.IntegrityError: flash("Сотрудник уже состоит в этой группе", "warning")
        elif "remove_member" in request.form:
            emp_id = request.form.get("employee_id")
            db.execute("DELETE FROM group_members WHERE group_id=? AND employee_id=?", (group_id, emp_id))
            db.commit(); log_action("Удален из группы", details=f"Группа {group['name']}, Сотрудник ID {emp_id}")
            flash("Сотрудник удален из группы", "warning")
        return redirect(url_for("main.admin_chat_group_edit", group_id=group_id))
    members = db.execute("""SELECT e.id, e.full_name FROM group_members gm JOIN employees e ON gm.employee_id = e.id WHERE gm.group_id = ?""", (group_id,)).fetchall()
    member_ids = [m["id"] for m in members]
    if member_ids:
        placeholders = ','.join('?' * len(member_ids))
        available_employees = db.execute(f"""SELECT id, full_name FROM employees WHERE chat_enabled=1 AND is_deleted=0 AND id NOT IN ({placeholders}) ORDER BY full_name""", member_ids).fetchall()
    else:
        available_employees = db.execute("""SELECT id, full_name FROM employees WHERE chat_enabled=1 AND is_deleted=0 ORDER BY full_name""").fetchall()
    return render_template("admin_chat_group_form.html", group=group, members=members, available=available_employees)

@main_bp.route("/admin/chat_groups/<int:group_id>/delete", methods=["POST"])
@login_required(roles=("admin",))
def admin_chat_group_delete(group_id):
    db = get_db()
    group = db.execute("SELECT name FROM chat_groups WHERE id=?", (group_id,)).fetchone()
    if group:
        db.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
        db.execute("DELETE FROM messages WHERE group_id=?", (group_id,))
        db.execute("DELETE FROM chat_groups WHERE id=?", (group_id,))
        db.commit(); log_action("Удалена группа чата", details=group["name"])
        flash("Группа удалена", "success")
    return redirect(url_for("main.admin_chat_groups"))