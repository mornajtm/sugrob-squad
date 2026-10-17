from flask import Flask, request, jsonify, render_template, redirect
import psycopg2
import psycopg2.extras
import hashlib
import secrets
import os
import pathlib
import urllib.request
import time
import json as json_lib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = pathlib.Path(__file__).parent

def _clean_db_url(url):
    if not url:
        return url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    try:
        parsed = urlparse(url)
        allowed = {"sslmode", "connect_timeout", "application_name"}
        query = dict(parse_qsl(parsed.query))
        clean_query = {k: v for k, v in query.items() if k in allowed}
        cleaned = parsed._replace(query=urlencode(clean_query))
        return urlunparse(cleaned)
    except Exception:
        return url

DATABASE_URL = _clean_db_url(os.environ.get("DATABASE_URL", ""))

ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXT

app = Flask(__name__)

# Безопасность: логины и пароли админов берутся из переменных окружения Vercel (.env локально)
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Vladimir")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN2_USERNAME = os.environ.get("ADMIN2_USERNAME", "Wito")
ADMIN2_PASSWORD = os.environ.get("ADMIN2_PASSWORD", "")

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")
DISCORD_ROLE_ID = os.environ.get("DISCORD_ROLE_ID", "")
DISCORD_REDIRECT = os.environ.get("DISCORD_REDIRECT", "https://sugrobsquad.vercel.app/auth/discord/callback")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

def send_discord(text):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        data = json_lib.dumps({"content": text}).encode("utf-8")
        req = urllib.request.Request(DISCORD_WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("Discord webhook error:", e)

def ensure_admin(cur):
    if not ADMIN_PASSWORD or not ADMIN2_PASSWORD:
        print("⚠️ Пароли админов не заданы в переменных окружения!")
        return

    admins = [(ADMIN_USERNAME, ADMIN_PASSWORD), (ADMIN2_USERNAME, ADMIN2_PASSWORD)]
    for username, password in admins:
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        if row:
            uid = row["id"] if isinstance(row, dict) else row[0]
            cur.execute("UPDATE users SET role = 'Администратор' WHERE id = %s", (uid,))
            continue
        cur.execute("INSERT INTO users (username, password, nickname, role, balance) VALUES (%s, %s, %s, %s, %s)",
                    (username, hash_pw(password), username, "Администратор", 999999))

def init_db():
    con = psycopg2.connect(DATABASE_URL)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            nickname TEXT,
            about TEXT,
            avatar TEXT,
            role TEXT DEFAULT 'Новичок',
            balance INTEGER DEFAULT 1000,
            is_banned BOOLEAN DEFAULT FALSE,
            discord_id TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
    ucols = [row[0] for row in cur.fetchall()]
    if "balance" not in ucols: cur.execute("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 1000")
    if "is_banned" not in ucols: cur.execute("ALTER TABLE users ADD COLUMN is_banned BOOLEAN DEFAULT FALSE")
    if "discord_id" not in ucols: cur.execute("ALTER TABLE users ADD COLUMN discord_id TEXT DEFAULT NULL")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            seller_id INTEGER NOT NULL,
            type TEXT DEFAULT 'shop',
            shop TEXT,
            item TEXT NOT NULL,
            description TEXT,
            quantity INTEGER DEFAULT 1,
            per_slot INTEGER DEFAULT 1,
            measure TEXT DEFAULT 'Штук',
            price INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'products'")
    pcols = [row[0] for row in cur.fetchall()]
    if "per_slot" not in pcols: cur.execute("ALTER TABLE products ADD COLUMN per_slot INTEGER DEFAULT 1")
    if "updated_at" not in pcols: cur.execute("ALTER TABLE products ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id SERIAL PRIMARY KEY,
            product_id INTEGER,
            seller_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            per_slot INTEGER DEFAULT 1,
            measure TEXT DEFAULT 'Штук',
            total INTEGER NOT NULL,
            shop TEXT,
            map TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS plots (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            map TEXT,
            title TEXT NOT NULL,
            x INTEGER DEFAULT 0,
            z INTEGER DEFAULT 0,
            price INTEGER DEFAULT 1,
            status TEXT DEFAULT 'free',
            kind TEXT DEFAULT 'rent',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'plots'")
    cols = [row[0] for row in cur.fetchall()]
    if "kind" not in cols: cur.execute("ALTER TABLE plots ADD COLUMN kind TEXT DEFAULT 'rent'")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS places (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            x INTEGER DEFAULT 0,
            z INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("DELETE FROM products WHERE shop IS NULL OR shop = '' OR shop NOT IN (SELECT title FROM plots WHERE kind = 'shop')")
    ensure_admin(cur)
    con.commit()
    cur.close()
    con.close()

def db():
    con = psycopg2.connect(DATABASE_URL)
    con.cursor_factory = psycopg2.extras.RealDictCursor
    return con

SESSIONS = {}

def make_token(uid):
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = uid
    return token

def current_user():
    token = request.cookies.get("token")
    if not token or token not in SESSIONS:
        return None
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (SESSIONS[token],))
    user = cur.fetchone()
    cur.close()
    con.close()
    return user

def is_admin(u):
    if not u: return False
    return u.get("role") == "Администратор" or u.get("username") in ("Vladimir", "Wito")

def require_admin(u):
    if not is_admin(u): return jsonify({"error": "Доступ только для админа"}), 403
    return None

@app.route("/")
def home():
    return render_template("index.html", user=current_user())

@app.route("/profile")
def profile_page():
    if not current_user(): return redirect("/")
    return render_template("profile.html")

@app.route("/trade")
def trade_page():
    return render_template("trade.html", user=current_user())

@app.route("/realty")
def realty_page():
    return render_template("realty.html", user=current_user())

@app.route("/places")
def places_page():
    return render_template("places.html", user=current_user())

@app.route("/organizations")
def organizations_page():
    return render_template("organizations.html", user=current_user())

@app.route("/docs")
def docs_page():
    return render_template("docs.html", user=current_user())

@app.route("/admin")
def admin_page():
    u = current_user()
    if not is_admin(u): return redirect("/")
    return render_template("admin.html", user=u)

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password: return jsonify({"error": "Заполни все поля"}), 400
    if len(password) < 6: return jsonify({"error": "Пароль минимум 6 символов"}), 400
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    if cur.fetchone():
        cur.close(); con.close()
        return jsonify({"error": "Такой ник уже занят"}), 400
    cur.execute("INSERT INTO users (username, password, nickname, balance) VALUES (%s, %s, %s, %s) RETURNING id",
                (username, hash_pw(password), username, 1000))
    uid = cur.fetchone()["id"]
    con.commit(); cur.close(); con.close()
    send_discord("Новый игрок зарегистрировался: " + username)
    token = make_token(uid)
    res = jsonify({"success": True})
    res.set_cookie("token", token, httponly=True, samesite="Lax", max_age=60*60*24*7)
    return res

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    cur.close(); con.close()
    if not user or user["password"] != hash_pw(password):
        return jsonify({"error": "Неверный ник или пароль"}), 401
    if user.get("is_banned"):
        return jsonify({"error": "Аккаунт заблокирован"}), 403
    token = make_token(user["id"])
    res = jsonify({"success": True})
    res.set_cookie("token", token, httponly=True, samesite="Lax", max_age=60*60*24*7)
    return res

@app.route("/api/logout", methods=["POST"])
def api_logout():
    token = request.cookies.get("token")
    SESSIONS.pop(token, None)
    res = jsonify({"success": True})
    res.set_cookie("token", "", max_age=0)
    return res

@app.route("/api/profile", methods=["GET"])
def api_get_profile():
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    return jsonify({"user": {
        "id": u["id"], "username": u["username"],
        "nickname": u["nickname"] or u["username"],
        "about": u["about"] or "", "avatar": u["avatar"] or "",
        "role": u["role"], "balance": u.get("balance", 0),
        "created_at": u["created_at"].isoformat() if u["created_at"] else "",
    }})

@app.route("/api/profile", methods=["PUT"])
def api_update_profile():
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    data = request.get_json() or {}
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET nickname = %s, about = %s, avatar = %s WHERE id = %s",
                (data.get("nickname", ""), data.get("about", ""), data.get("avatar", ""), u["id"]))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/products", methods=["GET"])
def api_products():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT p.*, u.username, u.nickname, u.avatar FROM products p JOIN users u ON u.id = p.seller_id ORDER BY p.updated_at DESC, p.created_at DESC")
    rows = cur.fetchall()
    cur.close(); con.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
        if d.get("updated_at"): d["updated_at"] = d["updated_at"].isoformat()
        result.append(d)
    return jsonify({"products": result})

@app.route("/api/products", methods=["POST"])
def api_create_product():
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    data = request.get_json() or {}
    item = (data.get("item") or "").strip()
    shop = data.get("shop", "")
    if not item: return jsonify({"error": "Укажи предмет"}), 400
    if not shop: return jsonify({"error": "Выбери магазин"}), 400
    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO products (seller_id, type, shop, item, description, quantity, per_slot, measure, price) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (u["id"], data.get("type", "shop"), shop, item, data.get("description", ""),
         int(data.get("quantity") or 1), int(data.get("per_slot") or 1),
         data.get("measure", "Штук"), int(data.get("price") or 1)))
    con.commit(); cur.close(); con.close()
    send_discord("Товар выставлен: " + item)
    return jsonify({"success": True})

@app.route("/api/products/<int:pid>", methods=["DELETE"])
def api_delete_product(pid):
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM products WHERE id = %s AND seller_id = %s", (pid, u["id"]))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/plots", methods=["GET"])
def api_plots():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT p.*, u.username, u.nickname FROM plots p JOIN users u ON u.id = p.owner_id ORDER BY p.created_at DESC")
    rows = cur.fetchall()
    cur.close(); con.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return jsonify({"plots": result})

@app.route("/api/plots", methods=["POST"])
def api_create_plot():
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title: return jsonify({"error": "Укажи название"}), 400
    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO plots (owner_id, map, title, x, z, price, status, kind) VALUES (%s, %s, %s, %s, %s, %s, 'free', %s)",
        (u["id"], data.get("map", ""), title, int(data.get("x") or 0), int(data.get("z") or 0),
         int(data.get("price") or 1), data.get("kind", "rent")))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/plots/<int:pid>/rent", methods=["POST"])
def api_rent_plot(pid):
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM plots WHERE id = %s", (pid,))
    plot = cur.fetchone()
    if not plot:
        cur.close(); con.close()
        return jsonify({"error": "Объект не найден"}), 404
    if plot["status"] == "rented":
        cur.close(); con.close()
        return jsonify({"error": "Уже арендован"}), 400
    cur.execute("UPDATE plots SET status = 'rented' WHERE id = %s", (pid,))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/my-shops", methods=["GET"])
def api_my_shops():
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, title, map, x, z FROM plots WHERE owner_id = %s AND kind = 'shop' ORDER BY created_at DESC", (u["id"],))
    rows = cur.fetchall()
    cur.close(); con.close()
    return jsonify({"shops": [dict(r) for r in rows]})

@app.route("/api/places", methods=["GET"])
def api_places():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT p.*, u.username, u.nickname, u.avatar FROM places p JOIN users u ON u.id = p.owner_id ORDER BY p.created_at DESC")
    rows = cur.fetchall()
    cur.close(); con.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return jsonify({"places": result})

@app.route("/api/places", methods=["POST"])
def api_create_place():
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title: return jsonify({"error": "Укажи название"}), 400
    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO places (owner_id, title, x, z) VALUES (%s, %s, %s, %s)",
        (u["id"], title, int(data.get("x") or 0), int(data.get("z") or 0)))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/places/<int:pid>", methods=["DELETE"])
def api_delete_place(pid):
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM places WHERE id = %s AND owner_id = %s", (pid, u["id"]))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/org/apply", methods=["POST"])
def api_org_apply():
    u = current_user()
    if not u: return jsonify({"error": "Не авторизован"}), 401
    data = request.get_json() or {}
    mc_nick = (data.get("mc_nick") or "").strip()
    reason = (data.get("reason") or "").strip()
    if not mc_nick or not reason:
        return jsonify({"error": "Заполни обязательные поля"}), 400
    org_names = {"economy": "Министерство Экономики", "defense": "Министерство Обороны", "hr": "Министерство HR"}
    org_name = org_names.get(data.get("org", ""), data.get("org", ""))
    send_discord("Новая заявка в " + org_name + " от " + (u["nickname"] or u["username"]))
    return jsonify({"success": True})

@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    u = current_user()
    err = require_admin(u)
    if err: return err
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, username, nickname, avatar, role, balance, is_banned, created_at FROM users ORDER BY id ASC")
    rows = cur.fetchall()
    cur.close(); con.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"): d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return jsonify({"users": result})

@app.route("/api/admin/users/<int:uid>/password", methods=["POST"])
def admin_change_password(uid):
    u = current_user()
    err = require_admin(u)
    if err: return err
    data = request.get_json() or {}
    new_pass = (data.get("password") or "").strip()
    if len(new_pass) < 6: return jsonify({"error": "Пароль минимум 6 символов"}), 400
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET password = %s WHERE id = %s", (hash_pw(new_pass), uid))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/admin/users/<int:uid>/ban", methods=["POST"])
def admin_ban_user(uid):
    u = current_user()
    err = require_admin(u)
    if err: return err
    if uid == u["id"]: return jsonify({"error": "Нельзя забанить себя"}), 400
    data = request.get_json() or {}
    ban = bool(data.get("ban"))
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET is_banned = %s WHERE id = %s", (ban, uid))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/admin/users/<int:uid>/balance", methods=["POST"])
def admin_change_balance(uid):
    u = current_user()
    err = require_admin(u)
    if err: return err
    data = request.get_json() or {}
    try:
        amount = int(data.get("amount") or 0)
    except:
        return jsonify({"error": "Неверная сумма"}), 400
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, uid))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
def admin_delete_user(uid):
    u = current_user()
    err = require_admin(u)
    if err: return err
    if uid == u["id"]: return jsonify({"error": "Нельзя удалить себя"}), 400
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/admin/products/<int:pid>", methods=["DELETE"])
def admin_delete_product(pid):
    u = current_user()
    err = require_admin(u)
    if err: return err
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/admin/plots/<int:pid>", methods=["DELETE"])
def admin_delete_plot(pid):
    u = current_user()
    err = require_admin(u)
    if err: return err
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM plots WHERE id = %s", (pid,))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/admin/places/<int:pid>", methods=["DELETE"])
def admin_delete_place(pid):
    u = current_user()
    err = require_admin(u)
    if err: return err
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM places WHERE id = %s", (pid,))
    con.commit(); cur.close(); con.close()
    return jsonify({"success": True})

@app.route("/api/health")
def api_health():
    info = {
        "db_url_present": bool(DATABASE_URL),
        "db_url_scheme": DATABASE_URL.split("://")[0] if DATABASE_URL else None,
        "db_url_host": DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "—",
    }
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users")
        row = cur.fetchone()
        info["users_count"] = row["c"] if isinstance(row, dict) else row[0]
        cur.close(); con.close()
        info["db_status"] = "OK"
    except Exception as e:
        info["db_status"] = "ERROR"
        info["db_error"] = str(e)
    return jsonify(info)

try:
    init_db()
    print("✅ База данных инициализирована")
except Exception as e:
    print("⚠️ Ошибка БД:", e)
    print("Продолжаем без БД — часть функций не будет работать")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Запуск на http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port)
