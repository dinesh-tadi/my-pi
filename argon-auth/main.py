import os
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

import jwt
import httpx
from fastapi import FastAPI, HTTPException, Depends, Header, Cookie, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import auth_db

JWT_SECRET = os.getenv("JWT_SECRET", "argon-pi-jwt-secret-key-super-secure-change-me-2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = int(os.getenv("JWT_EXPIRATION_DAYS", "30"))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
INITIAL_ADMIN_PASSWORD = os.getenv("INITIAL_ADMIN_PASSWORD", "ArgonAdmin2026!")
ALLOW_GOOGLE_SIGNUP = os.getenv("ALLOW_GOOGLE_SIGNUP", "true").lower() == "true"
APP_VERSION = "2.0.0"

app = FastAPI(title="Argon Hub Auth & Identity Gateway", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    auth_db.init_db(INITIAL_ADMIN_PASSWORD)

def create_access_token(user: Dict[str, Any]) -> str:
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRATION_DAYS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None

def get_current_user(
    authorization: Optional[str] = Header(None),
    argon_token: Optional[str] = Cookie(None)
) -> Dict[str, Any]:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
    elif argon_token:
        token = argon_token

    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = auth_db.get_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="User account not found or removed")

    return user

def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


# --- Pydantic Request Models ---

class LoginRequest(BaseModel):
    login: str
    password: str

class GoogleAuthRequest(BaseModel):
    id_token: str

class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: Optional[str] = None
    role: str = "user"
    display_name: Optional[str] = None

class RoleUpdateRequest(BaseModel):
    role: str

class PasswordResetRequest(BaseModel):
    new_password: str


# --- Public Authentication Endpoints ---

@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response):
    user = auth_db.get_user_by_login(req.login)
    if not user or not auth_db.verify_password(req.password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    auth_db.update_last_login(user["id"])
    token = create_access_token(user)

    # Set HTTP-only session cookie for browser requests
    response.set_cookie(
        key="argon_token",
        value=token,
        max_age=JWT_EXPIRATION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=False
    )

    clean_user = {k: user[k] for k in ["id", "username", "email", "role", "display_name"]}
    return {"token": token, "user": clean_user}

@app.post("/api/auth/google")
def google_auth(req: GoogleAuthRequest, response: Response):
    # Verify Google token via Google TokenInfo API
    try:
        res = httpx.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={req.id_token}",
            timeout=5.0
        )
        if res.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid Google ID token")
        payload = res.json()
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Google token verification failed: {str(e)}")

    if GOOGLE_CLIENT_ID and payload.get("aud") != GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Google client ID mismatch")

    email = payload.get("email")
    google_id = payload.get("sub")
    name = payload.get("name")

    if not email:
        raise HTTPException(status_code=400, detail="Google token does not contain an email address")

    # Match existing user by Google ID or Email
    user = auth_db.get_user_by_google_id(google_id)
    if not user:
        user = auth_db.get_user_by_email(email)
        if user:
            # Link Google ID to existing account
            auth_db.link_google_id(user["id"], google_id)
            user["google_id"] = google_id

    if not user:
        if not ALLOW_GOOGLE_SIGNUP:
            raise HTTPException(
                status_code=403,
                detail="New user self-registration via Google is disabled. Please request an admin to create your account."
            )
        # Create new user profile with role='user'
        base_username = email.split("@")[0].lower()
        candidate = base_username
        idx = 1
        while auth_db.get_user_by_login(candidate):
            candidate = f"{base_username}{idx}"
            idx += 1

        user = auth_db.create_user(
            username=candidate,
            email=email,
            password=None,
            role="user",
            display_name=name,
            google_id=google_id
        )

    auth_db.update_last_login(user["id"])
    token = create_access_token(user)

    response.set_cookie(
        key="argon_token",
        value=token,
        max_age=JWT_EXPIRATION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=False
    )

    clean_user = {k: user[k] for k in ["id", "username", "email", "role", "display_name"]}
    return {"token": token, "user": clean_user}

@app.get("/api/auth/me")
def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    clean_user = {k: user[k] for k in ["id", "username", "email", "role", "display_name", "created_at", "last_login"]}
    return {"user": clean_user}

@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie("argon_token")
    return {"message": "Logged out successfully"}

# Forward-Auth Endpoint (For Nginx / Caddy / Traefik Gateway Protection)
@app.get("/api/auth/verify")
def verify_forward_auth(request: Request, user: Dict[str, Any] = Depends(get_current_user)):
    return Response(
        content="OK",
        status_code=200,
        headers={
            "X-Auth-User": user["username"],
            "X-Auth-Email": user["email"],
            "X-Auth-Role": user["role"],
            "X-Auth-Id": str(user["id"])
        }
    )

# --- Admin APIs (RBAC Restricted to role == 'admin') ---

@app.get("/api/admin/users")
def admin_list_users(admin: Dict[str, Any] = Depends(require_admin)):
    return {"users": auth_db.list_all_users()}

@app.post("/api/admin/users")
def admin_create_user(req: CreateUserRequest, admin: Dict[str, Any] = Depends(require_admin)):
    if req.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")

    if auth_db.get_user_by_login(req.username):
        raise HTTPException(status_code=400, detail=f"Username '{req.username}' is already in use")

    if auth_db.get_user_by_email(req.email):
        raise HTTPException(status_code=400, detail=f"Email '{req.email}' is already registered")

    new_user = auth_db.create_user(
        username=req.username,
        email=req.email,
        password=req.password,
        role=req.role,
        display_name=req.display_name
    )
    return {"user": new_user, "message": "User created successfully"}

@app.patch("/api/admin/users/{user_id}/role")
def admin_update_role(user_id: int, req: RoleUpdateRequest, admin: Dict[str, Any] = Depends(require_admin)):
    if req.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")

    target = auth_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target["id"] == admin["id"] and req.role != "admin":
        raise HTTPException(status_code=400, detail="You cannot demote your own admin account")

    auth_db.update_user_role(user_id, req.role)
    return {"message": f"Role for '{target['username']}' updated to '{req.role}'"}

@app.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: int, req: PasswordResetRequest, admin: Dict[str, Any] = Depends(require_admin)):
    target = auth_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    auth_db.update_user_password(user_id, req.new_password)
    return {"message": "Password reset successfully"}

@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: Dict[str, Any] = Depends(require_admin)):
    target = auth_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target["id"] == admin["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own admin account")

    auth_db.delete_user(user_id)
    return {"message": f"User '{target['username']}' deleted successfully"}


# --- Version Info Endpoint ---

@app.get("/api/version")
def get_version():
    return {
        "version": APP_VERSION,
        "suite": "Argon Homelab Suite v2.0",
        "codename": "Argon Multi-User RBAC",
        "mobile_target": "2.0"
    }


# --- Web UI Pages (Embedded Modern Dark Mode) ---

def render_dashboard_html(user: Dict[str, Any]) -> str:
    is_admin = user.get("role") == "admin"
    username = user.get("username", "user")
    display_name = user.get("display_name") or username
    role_str = "ADMIN" if is_admin else "USER"
    role_badge_class = (
        "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
        if is_admin
        else "bg-cyan-500/15 text-cyan-400 border-cyan-500/30"
    )

    admin_nav_buttons = ""
    if is_admin:
        admin_nav_buttons = """
        <a href="/admin" class="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-purple-500/10 text-purple-400 border border-purple-500/20 hover:bg-purple-500/20 transition">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
          <span>Manage Users</span>
        </a>
        <a href="#" data-port="8082" class="service-card-link inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 transition">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>
          <span>System HUD</span>
        </a>
        """

    admin_section_html = ""
    if is_admin:
        admin_section_html = """
        <!-- Admin Restricted Section -->
        <div class="mt-12">
          <div class="flex items-center space-x-3 mb-6">
            <div class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></div>
            <h2 class="text-lg font-bold tracking-tight text-white uppercase text-xs tracking-wider text-slate-400">Core Infrastructure & System Portals (Admin Restricted)</h2>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">

            <!-- AdGuard Home -->
            <a href="#" data-port="8080" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-emerald-500/50 hover:shadow-xl hover:shadow-emerald-500/5 transition">
              <div class="flex items-start justify-between">
                <div class="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400 text-xl font-bold">🛡️</div>
                <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 8080</span>
              </div>
              <h3 class="text-base font-bold text-white mt-4 group-hover:text-emerald-400 transition">AdGuard Home</h3>
              <p class="text-xs text-slate-400 mt-1 line-clamp-2">Network-wide ad, tracker, & malware DNS filtering firewall.</p>
            </a>

            <!-- Uptime Kuma -->
            <a href="#" data-port="3001" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-emerald-500/50 hover:shadow-xl hover:shadow-emerald-500/5 transition">
              <div class="flex items-start justify-between">
                <div class="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400 text-xl font-bold">📈</div>
                <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 3001</span>
              </div>
              <h3 class="text-base font-bold text-white mt-4 group-hover:text-emerald-400 transition">Uptime Kuma</h3>
              <p class="text-xs text-slate-400 mt-1 line-clamp-2">Real-time service health, HTTP ping, and uptime monitoring HUD.</p>
            </a>

            <!-- Beszel Telemetry -->
            <a href="#" data-port="8090" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-emerald-500/50 hover:shadow-xl hover:shadow-emerald-500/5 transition">
              <div class="flex items-start justify-between">
                <div class="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400 text-xl font-bold">⚡</div>
                <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 8090</span>
              </div>
              <h3 class="text-base font-bold text-white mt-4 group-hover:text-emerald-400 transition">Beszel Telemetry</h3>
              <p class="text-xs text-slate-400 mt-1 line-clamp-2">Hardware thermals, CPU load, RAM utilization, and disk I/O metrics.</p>
            </a>

            <!-- Docker Registry -->
            <a href="#" data-port="5001" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-emerald-500/50 hover:shadow-xl hover:shadow-emerald-500/5 transition">
              <div class="flex items-start justify-between">
                <div class="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400 text-xl font-bold">🐳</div>
                <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 5001</span>
              </div>
              <h3 class="text-base font-bold text-white mt-4 group-hover:text-emerald-400 transition">Docker Registry</h3>
              <p class="text-xs text-slate-400 mt-1 line-clamp-2">Private ARM64 container image catalog and distribution hub.</p>
            </a>

            <!-- Qdrant Vector DB -->
            <a href="#" data-port="6333" data-path="/dashboard" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-emerald-500/50 hover:shadow-xl hover:shadow-emerald-500/5 transition">
              <div class="flex items-start justify-between">
                <div class="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400 text-xl font-bold">🧠</div>
                <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 6333</span>
              </div>
              <h3 class="text-base font-bold text-white mt-4 group-hover:text-emerald-400 transition">Qdrant Vector DB</h3>
              <p class="text-xs text-slate-400 mt-1 line-clamp-2">High-dimensional vector search engine for private local AI embeddings.</p>
            </a>

            <!-- Identity & Access Portal -->
            <a href="/admin" class="group block p-5 rounded-2xl bg-slate-900/80 border border-purple-500/30 hover:border-purple-500/60 hover:shadow-xl hover:shadow-purple-500/5 transition">
              <div class="flex items-start justify-between">
                <div class="w-12 h-12 rounded-xl bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-purple-400 text-xl font-bold">👥</div>
                <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30">Admin Core</span>
              </div>
              <h3 class="text-base font-bold text-white mt-4 group-hover:text-purple-400 transition">User Management Portal</h3>
              <p class="text-xs text-slate-400 mt-1 line-clamp-2">Add external users, toggle user/admin roles, and reset credentials.</p>
            </a>

          </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Argon Homelab Suite v2.0</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body {{ background-color: #0b0f19; }}
  </style>
</head>
<body class="text-slate-100 min-h-screen">
  <!-- Top Navigation -->
  <header class="border-b border-slate-800 bg-slate-900/60 backdrop-blur sticky top-0 z-30">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-600 to-purple-600 flex items-center justify-center font-bold text-white shadow-lg shadow-cyan-500/20">
          A
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-white text-base">Argon Hub</span>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded-full font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">v2.0</span>
          </div>
          <p class="text-[11px] text-slate-400 hidden sm:block">Zero-Trust Tailscale Homelab Suite</p>
        </div>
      </div>

      <!-- Right controls -->
      <div class="flex items-center space-x-3">
        <a href="#" data-port="5050" data-path="/argon-hub.apk" class="service-card-link inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 hover:bg-cyan-500/20 transition">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          <span class="hidden sm:inline">Get Mobile App</span>
        </a>
        {admin_nav_buttons}
        <div class="flex items-center space-x-2 px-3 py-1.5 rounded-xl bg-slate-950 border border-slate-800">
          <div class="w-6 h-6 rounded-lg bg-gradient-to-tr from-cyan-500 to-purple-500 flex items-center justify-center text-xs font-bold text-white">
            {username[0].upper()}
          </div>
          <span class="text-xs font-medium text-slate-200 hidden md:inline">{display_name}</span>
          <span class="text-[10px] font-mono px-1.5 py-0.5 rounded border {role_badge_class} font-bold">{role_str}</span>
        </div>
        <button onclick="handleLogout()" class="p-2 rounded-xl text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 transition" title="Sign Out">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
        </button>
      </div>
    </div>
  </header>

  <!-- Main Body -->
  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <!-- Greeting Banner -->
    <div class="p-6 rounded-3xl bg-gradient-to-r from-slate-900 via-slate-900/90 to-cyan-950/40 border border-slate-800 relative overflow-hidden mb-8">
      <div class="absolute -right-12 -top-12 w-48 h-48 bg-cyan-500/10 rounded-full blur-3xl"></div>
      <div class="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 class="text-2xl font-extrabold text-white">Welcome back, {display_name}!</h1>
          <p class="text-xs sm:text-sm text-slate-400 mt-1">
            {"You have full administrative privileges to manage infrastructure, DNS, and user access." if is_admin else "You have secure member access to your private daily productivity tools and encrypted storage."}
          </p>
        </div>
        <div class="flex items-center space-x-2 text-xs font-mono text-slate-400 bg-slate-950/60 px-3.5 py-2 rounded-xl border border-slate-800 w-fit">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          <span>Mesh Gateway Active</span>
        </div>
      </div>
    </div>

    <!-- Productivity & Privacy Section (Available to ALL authenticated users) -->
    <div>
      <div class="flex items-center space-x-3 mb-6">
        <div class="w-2.5 h-2.5 rounded-full bg-cyan-400"></div>
        <h2 class="text-lg font-bold tracking-tight text-white uppercase text-xs tracking-wider text-slate-400">Productivity & Privacy Workspace</h2>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">

        <!-- Argon Mobile Hub -->
        <a href="#" data-port="5050" data-path="/argon-hub.apk" class="service-card-link group block p-5 rounded-2xl bg-gradient-to-b from-slate-900 to-slate-900/90 border border-cyan-500/30 hover:border-cyan-400 hover:shadow-xl hover:shadow-cyan-500/10 transition relative overflow-hidden">
          <div class="flex items-start justify-between">
            <div class="w-12 h-12 rounded-xl bg-gradient-to-tr from-cyan-600/20 to-purple-600/20 border border-cyan-500/30 flex items-center justify-center text-xl font-bold">📱</div>
            <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-cyan-500/15 text-cyan-400 border border-cyan-500/30 font-semibold">Download APK</span>
          </div>
          <h3 class="text-base font-bold text-white mt-4 group-hover:text-cyan-400 transition flex items-center space-x-1.5">
            <span>Argon Mobile Hub</span>
            <svg class="w-4 h-4 text-cyan-400 opacity-0 group-hover:opacity-100 transition" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          </h3>
          <p class="text-xs text-slate-400 mt-1 line-clamp-2">Direct download Android client (v2.0) with Tailscale auto-mesh & RBAC.</p>
        </a>

        <!-- Vaultwarden -->
        <a href="#" data-port="8443" data-scheme="https" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-cyan-500/50 hover:shadow-xl hover:shadow-cyan-500/5 transition">
          <div class="flex items-start justify-between">
            <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 text-xl font-bold">🔐</div>
            <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 8443</span>
          </div>
          <h3 class="text-base font-bold text-white mt-4 group-hover:text-cyan-400 transition">Vaultwarden</h3>
          <p class="text-xs text-slate-400 mt-1 line-clamp-2">Encrypted password vault, 2FA authenticator, and secure notes manager.</p>
        </a>

        <!-- Stirling-PDF -->
        <a href="#" data-port="8086" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-cyan-500/50 hover:shadow-xl hover:shadow-cyan-500/5 transition">
          <div class="flex items-start justify-between">
            <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 text-xl font-bold">📄</div>
            <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 8086</span>
          </div>
          <h3 class="text-base font-bold text-white mt-4 group-hover:text-cyan-400 transition">Stirling-PDF</h3>
          <p class="text-xs text-slate-400 mt-1 line-clamp-2">In-browser private document studio: OCR, conversions, merge, and redaction.</p>
        </a>

        <!-- PairDrop -->
        <a href="#" data-port="8088" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-cyan-500/50 hover:shadow-xl hover:shadow-cyan-500/5 transition">
          <div class="flex items-start justify-between">
            <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 text-xl font-bold">📡</div>
            <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 8088</span>
          </div>
          <h3 class="text-base font-bold text-white mt-4 group-hover:text-cyan-400 transition">PairDrop</h3>
          <p class="text-xs text-slate-400 mt-1 line-clamp-2">Peer-to-peer AirDrop alternative for encrypted file transfers between devices.</p>
        </a>

        <!-- Syncthing -->
        <a href="#" data-port="8384" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-cyan-500/50 hover:shadow-xl hover:shadow-cyan-500/5 transition">
          <div class="flex items-start justify-between">
            <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 text-xl font-bold">🔄</div>
            <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 8384</span>
          </div>
          <h3 class="text-base font-bold text-white mt-4 group-hover:text-cyan-400 transition">Syncthing</h3>
          <p class="text-xs text-slate-400 mt-1 line-clamp-2">Decentralized real-time continuous folder and Obsidian notes sync.</p>
        </a>

        <!-- Disposable Browser -->
        <a href="#" data-port="3003" data-scheme="https" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-cyan-500/50 hover:shadow-xl hover:shadow-cyan-500/5 transition">
          <div class="flex items-start justify-between">
            <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 text-xl font-bold">🌐</div>
            <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 3003</span>
          </div>
          <h3 class="text-base font-bold text-white mt-4 group-hover:text-cyan-400 transition">Disposable Browser</h3>
          <p class="text-xs text-slate-400 mt-1 line-clamp-2">Isolated ephemeral Chromium sandbox via low-latency WebRTC stream.</p>
        </a>

        <!-- IT-Tools -->
        <a href="#" data-port="8087" target="_blank" class="service-card-link group block p-5 rounded-2xl bg-slate-900/80 border border-slate-800 hover:border-cyan-500/50 hover:shadow-xl hover:shadow-cyan-500/5 transition">
          <div class="flex items-start justify-between">
            <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 text-xl font-bold">🧰</div>
            <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Port 8087</span>
          </div>
          <h3 class="text-base font-bold text-white mt-4 group-hover:text-cyan-400 transition">IT-Tools</h3>
          <p class="text-xs text-slate-400 mt-1 line-clamp-2">80+ Handy developer tools: token decoders, regex testers, and formatters.</p>
        </a>

      </div>
    </div>

    {admin_section_html}

  </main>

  <script>
    // Bind all service links dynamically to the current hostname (works on Tailscale IP, MagicDNS, and LAN)
    document.querySelectorAll('.service-card-link').forEach(el => {{
      const port = el.getAttribute('data-port');
      const scheme = el.getAttribute('data-scheme') || 'http';
      const path = el.getAttribute('data-path') || '/';
      if (port) {{
        el.href = scheme + '://' + window.location.hostname + ':' + port + path;
      }}
    }});

    async function handleLogout() {{
      await fetch('/api/auth/logout', {{ method: 'POST' }});
      localStorage.removeItem('argon_token');
      localStorage.removeItem('argon_user');
      window.location.href = '/login';
    }}
  </script>
</body>
</html>"""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Argon Hub — Unified Authentication</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <style>
    body { background-color: #0b0f19; }
  </style>
</head>
<body class="text-slate-100 flex items-center justify-center min-h-screen px-4">
  <div class="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl relative overflow-hidden">
    <!-- Glow effect -->
    <div class="absolute -top-24 -left-24 w-48 h-48 bg-cyan-500/10 rounded-full blur-3xl"></div>
    <div class="absolute -bottom-24 -right-24 w-48 h-48 bg-purple-500/10 rounded-full blur-3xl"></div>

    <div class="text-center mb-8 relative">
      <div class="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-tr from-cyan-600 to-purple-600 mb-4 shadow-lg shadow-cyan-500/20">
        <svg class="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>
        </svg>
      </div>
      <h1 class="text-2xl font-bold tracking-tight text-white flex items-center justify-center">
        <span>Argon Hub Sign In</span>
        <span class="ml-2.5 text-xs px-2.5 py-0.5 rounded-full font-mono bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-semibold">v2.0</span>
      </h1>
      <p class="text-sm text-slate-400 mt-1">Homelab Identity & Access Gateway</p>
    </div>

    <div id="errorAlert" class="hidden mb-6 p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 text-sm"></div>

    <form id="loginForm" class="space-y-4">
      <div>
        <label class="block text-xs font-medium text-slate-300 uppercase tracking-wider mb-1.5">Username or Email</label>
        <input type="text" id="loginInput" required placeholder="admin or user@gmail.com"
          class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 transition">
      </div>
      <div>
        <label class="block text-xs font-medium text-slate-300 uppercase tracking-wider mb-1.5">Password</label>
        <input type="password" id="passwordInput" required placeholder="••••••••"
          class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 transition">
      </div>
      <button type="submit"
        class="w-full py-3.5 px-4 rounded-xl bg-gradient-to-r from-cyan-600 to-purple-600 hover:from-cyan-500 hover:to-purple-500 text-white font-semibold shadow-lg shadow-cyan-500/20 active:scale-[0.99] transition mt-2">
        Sign In
      </button>
    </form>

    <div class="relative my-6">
      <div class="absolute inset-0 flex items-center"><div class="w-full border-t border-slate-800"></div></div>
      <div class="relative flex justify-center text-xs uppercase"><span class="bg-slate-900 px-3 text-slate-500 font-mono">or continue with</span></div>
    </div>

    <!-- Google One-Tap / Button -->
    <div class="flex justify-center">
      <div id="g_id_onload"
           data-client_id="YOUR_GOOGLE_CLIENT_ID"
           data-callback="handleGoogleCredential"
           data-auto_prompt="false">
      </div>
      <div class="g_id_signin"
           data-type="standard"
           data-size="large"
           data-theme="filled_black"
           data-text="sign_in_with"
           data-shape="pill"
           data-logo_alignment="left">
      </div>
    </div>

    <p class="text-xs text-center text-slate-500 mt-6">
      Protected by Argon Zero-Trust Tailscale Network
    </p>
  </div>

  <script>
    const form = document.getElementById('loginForm');
    const errAlert = document.getElementById('errorAlert');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      errAlert.classList.add('hidden');
      const login = document.getElementById('loginInput').value.trim();
      const password = document.getElementById('passwordInput').value;

      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ login, password })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Login failed');

        localStorage.setItem('argon_token', data.token);
        localStorage.setItem('argon_user', JSON.stringify(data.user));

        window.location.href = '/';
      } catch (err) {
        errAlert.textContent = err.message;
        errAlert.classList.remove('hidden');
      }
    });

    async function handleGoogleCredential(response) {
      errAlert.classList.add('hidden');
      try {
        const res = await fetch('/api/auth/google', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id_token: response.credential })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Google authentication failed');

        localStorage.setItem('argon_token', data.token);
        localStorage.setItem('argon_user', JSON.stringify(data.user));

        window.location.href = '/';
      } catch (err) {
        errAlert.textContent = err.message;
        errAlert.classList.remove('hidden');
      }
    }
  </script>
</body>
</html>"""


ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Argon Hub — Admin User & Role Management</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background-color: #0b0f19; }
  </style>
</head>
<body class="text-slate-100 min-h-screen">
  <!-- Top Navigation -->
  <header class="border-b border-slate-800 bg-slate-900/60 backdrop-blur sticky top-0 z-30">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <a href="/" class="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-600 to-purple-600 flex items-center justify-center font-bold text-white shadow-lg shadow-cyan-500/20 hover:opacity-90 transition">
          A
        </a>
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-white text-base">Argon Identity Portal</span>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded-full font-semibold bg-purple-500/10 text-purple-400 border border-purple-500/20">ADMIN</span>
          </div>
          <p class="text-[11px] text-slate-400">Multi-User RBAC & Credentials Management</p>
        </div>
      </div>

      <div class="flex items-center space-x-3">
        <a href="/" class="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 transition">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"/></svg>
          <span>Back to Hub</span>
        </a>
        <button onclick="openAddUserModal()" class="px-3.5 py-1.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs shadow-lg shadow-cyan-500/20 transition flex items-center space-x-1.5">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          <span>Add User</span>
        </button>
        <button onclick="logout()" class="p-2 rounded-xl text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 transition" title="Sign Out">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
        </button>
      </div>
    </div>
  </header>

  <!-- Content Container -->
  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
      <div>
        <h1 class="text-2xl font-bold tracking-tight text-white">Registered Users & Access Roles</h1>
        <p class="text-sm text-slate-400 mt-1">Users added here can connect via Tailscale Node Sharing and authenticate to Argon Hub.</p>
      </div>
      <div class="flex items-center space-x-3 text-xs text-slate-400">
        <span class="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-full bg-slate-900 border border-slate-800">
          <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
          <span id="userCount">0 Total Users</span>
        </span>
      </div>
    </div>

    <!-- Users Table Card -->
    <div class="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
      <div class="overflow-x-auto">
        <table class="w-full text-left border-collapse text-sm">
          <thead>
            <tr class="border-b border-slate-800 bg-slate-950/50 text-slate-400 font-mono text-xs uppercase">
              <th class="py-4 px-6">User</th>
              <th class="py-4 px-6">Email / Google ID</th>
              <th class="py-4 px-6">Role</th>
              <th class="py-4 px-6">Last Active</th>
              <th class="py-4 px-6 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="userTableBody" class="divide-y divide-slate-800/60">
            <tr>
              <td colspan="5" class="py-8 text-center text-slate-500">Loading user profiles...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </main>

  <!-- Add User Modal -->
  <div id="addUserModal" class="hidden fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 shadow-2xl relative">
      <div class="flex items-center justify-between mb-5">
        <h3 class="text-lg font-bold text-white">Create New User Profile</h3>
        <button onclick="closeAddUserModal()" class="text-slate-400 hover:text-white transition">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <form id="addUserForm" class="space-y-4">
        <div>
          <label class="block text-xs font-medium text-slate-300 uppercase tracking-wider mb-1">Username *</label>
          <input type="text" id="newUsername" required placeholder="alice"
            class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500 text-sm">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-300 uppercase tracking-wider mb-1">Email * (for Google SSO or login)</label>
          <input type="email" id="newEmail" required placeholder="alice@gmail.com"
            class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500 text-sm">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-300 uppercase tracking-wider mb-1">Initial Password (optional)</label>
          <input type="password" id="newPassword" placeholder="••••••••"
            class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500 text-sm">
          <p class="text-[11px] text-slate-500 mt-1">Leave empty if user will only authenticate via Google OIDC.</p>
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-300 uppercase tracking-wider mb-1">Role *</label>
          <select id="newRole" class="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white focus:outline-none focus:border-cyan-500 text-sm">
            <option value="user" selected>User (Restricted to daily productivity tools)</option>
            <option value="admin">Admin (Full root/management privileges)</option>
          </select>
        </div>

        <div class="flex items-center justify-end space-x-3 pt-3">
          <button type="button" onclick="closeAddUserModal()" class="px-4 py-2 rounded-xl text-xs font-medium text-slate-400 hover:text-white transition">Cancel</button>
          <button type="submit" class="px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold shadow-lg shadow-cyan-500/20 transition">Create User</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    async function loadData() {
      try {
        const res = await fetch('/api/admin/users');
        if (res.status === 401 || res.status === 403) {
          window.location.href = '/login';
          return;
        }
        const data = await res.json();
        renderUsers(data.users || []);
      } catch (err) {
        console.error('Failed to load users:', err);
      }
    }

    function renderUsers(users) {
      document.getElementById('userCount').textContent = `${users.length} Total Users`;
      const tbody = document.getElementById('userTableBody');
      if (users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-500">No users found.</td></tr>';
        return;
      }

      tbody.innerHTML = users.map(u => {
        const isAdmin = u.role === 'admin';
        const roleBadge = isAdmin
          ? '<span class="px-2.5 py-1 rounded-full text-xs font-bold font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">ADMIN</span>'
          : '<span class="px-2.5 py-1 rounded-full text-xs font-bold font-mono bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">USER</span>';

        const lastLogin = u.last_login ? new Date(u.last_login).toLocaleString() : '<span class="text-slate-600">Never</span>';
        const googleTag = u.google_id
          ? '<span class="inline-flex items-center space-x-1 text-[11px] text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded border border-purple-500/20 font-mono">Google Linked</span>'
          : '<span class="text-[11px] text-slate-600">Local Only</span>';

        return `
          <tr class="hover:bg-slate-800/30 transition">
            <td class="py-4 px-6">
              <div class="flex items-center space-x-3">
                <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-600 to-purple-600 flex items-center justify-center font-bold text-white text-xs">
                  ${(u.username || 'U')[0].toUpperCase()}
                </div>
                <div>
                  <div class="font-semibold text-white">${u.display_name || u.username}</div>
                  <div class="text-xs text-slate-400 font-mono">@${u.username}</div>
                </div>
              </div>
            </td>
            <td class="py-4 px-6">
              <div class="text-slate-200 text-xs">${u.email}</div>
              <div class="mt-1">${googleTag}</div>
            </td>
            <td class="py-4 px-6">
              ${roleBadge}
            </td>
            <td class="py-4 px-6 text-xs text-slate-400 font-mono">
              ${lastLogin}
            </td>
            <td class="py-4 px-6 text-right">
              <div class="inline-flex items-center space-x-2">
                <button onclick="toggleRole(${u.id}, '${isAdmin ? 'user' : 'admin'}')"
                  class="px-2.5 py-1 rounded-lg text-xs font-medium ${isAdmin ? 'text-amber-400 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/20' : 'text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20'} transition">
                  ${isAdmin ? 'Demote to User' : 'Make Admin'}
                </button>
                <button onclick="promptResetPassword(${u.id}, '${u.username}')"
                  class="px-2.5 py-1 rounded-lg text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 border border-slate-700 transition">
                  Reset Pass
                </button>
                <button onclick="deleteUser(${u.id}, '${u.username}')"
                  class="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 transition" title="Delete User">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </button>
              </div>
            </td>
          </tr>
        `;
      }).join('');
    }

    async function toggleRole(userId, newRole) {
      if (!confirm(`Are you sure you want to change this user's role to '${newRole}'?`)) return;
      try {
        const res = await fetch(`/api/admin/users/${userId}/role`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: newRole })
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Failed to update role');
        }
        loadData();
      } catch (err) {
        alert(err.message);
      }
    }

    async function promptResetPassword(userId, username) {
      const newPass = prompt(`Enter new password for @${username} (min 6 characters):`);
      if (!newPass) return;
      if (newPass.length < 6) {
        alert('Password must be at least 6 characters long.');
        return;
      }

      try {
        const res = await fetch(`/api/admin/users/${userId}/reset-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_password: newPass })
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Failed to reset password');
        }
        alert(`Password for @${username} was reset successfully.`);
      } catch (err) {
        alert(err.message);
      }
    }

    async function deleteUser(userId, username) {
      if (!confirm(`Are you sure you want to delete user @${username}? This action is permanent.`)) return;
      try {
        const res = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Failed to delete user');
        }
        loadData();
      } catch (err) {
        alert(err.message);
      }
    }

    function openAddUserModal() {
      document.getElementById('addUserModal').classList.remove('hidden');
    }
    function closeAddUserModal() {
      document.getElementById('addUserModal').classList.add('hidden');
    }

    document.getElementById('addUserForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('newUsername').value.trim();
      const email = document.getElementById('newEmail').value.trim();
      const password = document.getElementById('newPassword').value || null;
      const role = document.getElementById('newRole').value;

      try {
        const res = await fetch('/api/admin/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, email, password, role })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to create user');

        closeAddUserModal();
        loadData();
      } catch (err) {
        alert(err.message);
      }
    });

    async function logout() {
      await fetch('/api/auth/logout', { method: 'POST' });
      localStorage.removeItem('argon_token');
      localStorage.removeItem('argon_user');
      window.location.href = '/login';
    }

    loadData();
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def hub_dashboard(request: Request, argon_token: Optional[str] = Cookie(None)):
    token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    elif argon_token:
        token = argon_token

    if not token:
        return RedirectResponse(url="/login", status_code=302)

    payload = decode_access_token(token)
    if not payload:
        return RedirectResponse(url="/login", status_code=302)

    user = auth_db.get_user_by_id(int(payload.get("sub", 0)))
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return HTMLResponse(content=render_dashboard_html(user))

@app.get("/login", response_class=HTMLResponse)
def login_view(request: Request, argon_token: Optional[str] = Cookie(None)):
    token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    elif argon_token:
        token = argon_token

    if token:
        payload = decode_access_token(token)
        if payload and auth_db.get_user_by_id(int(payload.get("sub", 0))):
            return RedirectResponse(url="/", status_code=302)

    return HTMLResponse(content=LOGIN_HTML)

@app.get("/admin", response_class=HTMLResponse)
def admin_view():
    return HTMLResponse(content=ADMIN_HTML)
