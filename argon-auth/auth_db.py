import os
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DB_DIR = os.getenv("AUTH_DB_DIR", "/data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "users.db")

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    return pwd_context.verify(plain_password, hashed_password)

def init_db(initial_admin_password: str = "ArgonAdmin2026!"):
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                google_id TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                display_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            );
        """)
        
        # Check if any user exists
        cursor = conn.execute("SELECT COUNT(*) AS count FROM users;")
        count = cursor.fetchone()["count"]
        if count == 0:
            admin_hash = hash_password(initial_admin_password)
            conn.execute("""
                INSERT INTO users (username, email, password_hash, role, display_name)
                VALUES (?, ?, ?, 'admin', 'System Administrator');
            """, ("admin", "admin@argon.local", admin_hash))
            conn.commit()
            print(f"[AUTH DB] Seeded initial admin account: 'admin' (Password: {initial_admin_password})")

def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?;", (user_id,)).fetchone()
        return dict(row) if row else None

def get_user_by_login(login: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?);",
            (login, login)
        ).fetchone()
        return dict(row) if row else None

def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?);", (email,)).fetchone()
        return dict(row) if row else None

def get_user_by_google_id(google_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE google_id = ?;", (google_id,)).fetchone()
        return dict(row) if row else None

def create_user(
    username: str,
    email: str,
    password: Optional[str] = None,
    role: str = "user",
    display_name: Optional[str] = None,
    google_id: Optional[str] = None
) -> Dict[str, Any]:
    pwd_hash = hash_password(password) if password else None
    disp = display_name or username
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO users (username, email, password_hash, google_id, role, display_name)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (username.strip(), email.strip().lower(), pwd_hash, google_id, role, disp))
        user_id = cursor.lastrowid
        conn.commit()
        return get_user_by_id(user_id)

def link_google_id(user_id: int, google_id: str):
    with get_connection() as conn:
        conn.execute("UPDATE users SET google_id = ? WHERE id = ?;", (google_id, user_id))
        conn.commit()

def update_last_login(user_id: int):
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?;", (now, user_id))
        conn.commit()

def list_all_users() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, username, email, role, display_name, google_id, created_at, last_login,
                   CASE WHEN password_hash IS NOT NULL THEN 1 ELSE 0 END as has_password
            FROM users
            ORDER BY id ASC;
        """).fetchall()
        return [dict(r) for r in rows]

def update_user_role(user_id: int, role: str):
    if role not in ("admin", "user"):
        raise ValueError("Role must be 'admin' or 'user'")
    with get_connection() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?;", (role, user_id))
        conn.commit()

def update_user_password(user_id: int, new_password: str):
    new_hash = hash_password(new_password)
    with get_connection() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?;", (new_hash, user_id))
        conn.commit()

def delete_user(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?;", (user_id,))
        conn.commit()
