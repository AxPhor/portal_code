import time
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from utils import get_db, log_action

auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("lockout_time"):
        time_left = int(session["lockout_time"]) - int(time.time())
        if time_left > 0:
            flash(f"Слишком много неудачных попыток. Попробуйте через {time_left} секунд.", "danger")
            return render_template("login.html")
        else:
            session.pop("lockout_time", None)
            session.pop("failed_attempts", None)

    if request.method == "POST":
        u = request.form["username"].strip()
        p = request.form["password"]
        row = get_db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        
        if row and check_password_hash(row["password_hash"], p):
            session.pop("failed_attempts", None)
            session.pop("lockout_time", None)
            session["username"] = row["username"]
            session["role"] = row["role"]
            log_action("Вход в систему")
            return redirect(request.args.get("next") or url_for("main.portal_home"))
        else:
            attempts = session.get("failed_attempts", 0) + 1
            session["failed_attempts"] = attempts
            if attempts >= 5:
                session["lockout_time"] = int(time.time()) + 30
                session["failed_attempts"] = 0
                flash("Слишком много неудачных попыток. Вход заблокирован на 30 секунд.", "danger")
            else:
                flash(f"Неверный логин или пароль. Осталось попыток: {5 - attempts}", "danger")   
    return render_template("login.html")

@auth_bp.route("/logout")
def logout():
    log_action("Выход из системы")
    session.clear()
    return redirect(url_for("main.portal_home"))