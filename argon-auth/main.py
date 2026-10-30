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
        raise HTTPException(status_code=401, detail="User account not found")
    return user

def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator privileges required")
    return user

# Pydantic Request Models
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

# --- Authentication APIs ---

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
    # Verify ID token with Google's public tokeninfo endpoint
    try:
        with httpx.Client(timeout=10) as client:
            res = client.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={req.id_token}")
            if res.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid Google token")
            google_data = res.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Google token verification failed: {str(e)}")

    email = google_data.get("email")
    google_id = google_data.get("sub")
    name = google_data.get("name") or email.split("@")[0]

    if not email:
        raise HTTPException(status_code=400, detail="Google token missing email")

    user = auth_db.get_user_by_email(email)

    if user:
        # Existing user by email: link google_id if not present
        if not user.get("google_id"):
            auth_db.link_google_id(user["id"], google_id)
            user["google_id"] = google_id
    else:
        # New user via Google
        if not ALLOW_GOOGLE_SIGNUP:
            raise HTTPException(
                status_code=403,
                detail="Google sign-up is disabled. Contact your administrator to invite your email first."
            )
        username = email.split("@")[0].replace(".", "_")
        # Ensure unique username
        existing = auth_db.get_user_by_login(username)
        if existing:
            username = f"{username}_{int(time.time()) % 1000}"

        user = auth_db.create_user(
            username=username,
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

# --- Forward-Auth Endpoint (For Nginx / Caddy / Traefik Gateway Protection) ---
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
    target = auth_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target["id"] == admin["id"] and req.role != "admin":
        raise HTTPException(status_code=400, detail="You cannot demote your own admin account")

    auth_db.update_user_role(user_id, req.role)
    return {"message": f"Role updated to {req.role}"}

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

@app.get("/", response_class=HTMLResponse)
@app.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse(content="""<!DOCTYPE html>
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

      <button type="submit" id="submitBtn"
        class="w-full py-3 px-4 rounded-xl font-medium text-white bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 transition shadow-lg shadow-cyan-500/25 active:scale-[0.98]">
        Sign In
      </button>
    </form>

    <div class="relative my-6 text-center">
      <div class="absolute inset-0 flex items-center"><div class="w-full border-t border-slate-800"></div></div>
      <span class="relative px-3 bg-slate-900 text-xs uppercase tracking-wider text-slate-500">Or continue with</span>
    </div>

    <!-- Google Sign In Container -->
    <div id="googleBtnWrapper" class="flex justify-center">
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

        if (data.user.role === 'admin') {
          window.location.href = '/admin';
        } else {
          window.location.href = 'http://' + window.location.hostname + '/';
        }
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

        if (data.user.role === 'admin') {
          window.location.href = '/admin';
        } else {
          window.location.href = 'http://' + window.location.hostname + '/';
        }
      } catch (err) {
        errAlert.textContent = err.message;
        errAlert.classList.remove('hidden');
      }
    }
  </script>
</body>
</html>
""")

@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return HTMLResponse(content="""<!DOCTYPE html>
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
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-600 to-purple-600 flex items-center justify-center font-bold text-white shadow-lg shadow-cyan-500/20">
          A
        </div>
        <div>
          <span class="font-bold text-lg text-white">Argon Hub</span>
          <span class="ml-2 text-xs px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/20 font-semibold uppercase tracking-wider">Admin Portal</span>
          <span class="ml-2 text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700 font-mono">v2.0</span>
        </div>
      </div>
      <div class="flex items-center space-x-4">
        <span id="currentAdminName" class="text-sm text-slate-300 font-medium"></span>
        <button onclick="logout()" class="text-xs px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-300 transition">
          Sign Out
        </button>
      </div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <!-- Stat Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-6 mb-8">
      <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl">
        <span class="text-xs font-medium text-slate-400 uppercase tracking-wider">Total Users</span>
        <p id="statTotalUsers" class="text-3xl font-bold text-white mt-2">-</p>
      </div>
      <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl">
        <span class="text-xs font-medium text-purple-400 uppercase tracking-wider">Administrators</span>
        <p id="statAdmins" class="text-3xl font-bold text-purple-400 mt-2">-</p>
      </div>
      <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl">
        <span class="text-xs font-medium text-cyan-400 uppercase tracking-wider">Standard Users</span>
        <p id="statUsers" class="text-3xl font-bold text-cyan-400 mt-2">-</p>
      </div>
    </div>

    <!-- Actions & User Table -->
    <div class="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
      <div class="p-6 border-b border-slate-800 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2 class="text-xl font-bold text-white">Registered User Directory</h2>
          <p class="text-sm text-slate-400">Manage credentials, Google profile linkage, and role-based permissions.</p>
        </div>
        <button onclick="openAddUserModal()" class="px-4 py-2.5 rounded-xl font-medium text-sm text-white bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 shadow-lg shadow-cyan-500/20 transition flex items-center space-x-2">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          <span>Add New User</span>
        </button>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm text-slate-300">
          <thead class="bg-slate-950 text-xs uppercase text-slate-400 border-b border-slate-800">
            <tr>
              <th class="px-6 py-4">User</th>
              <th class="px-6 py-4">Email</th>
              <th class="px-6 py-4">Role</th>
              <th class="px-6 py-4">Auth Methods</th>
              <th class="px-6 py-4">Created Date</th>
              <th class="px-6 py-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="userTableBody" class="divide-y divide-slate-800">
            <tr><td colspan="6" class="px-6 py-8 text-center text-slate-500">Loading user profiles...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Tailscale Sharing Guide Card -->
    <div class="mt-8 bg-slate-900 border border-slate-800 rounded-2xl p-6">
      <div class="flex items-center space-x-3 mb-4">
        <div class="w-8 h-8 rounded-lg bg-indigo-500/10 text-indigo-400 flex items-center justify-center font-bold">
          TS
        </div>
        <div>
          <h3 class="text-lg font-bold text-white">How to Grant Network Access via Tailscale</h3>
          <p class="text-xs text-slate-400">Allow users to connect to this Pi from their phone or laptop without opening ports.</p>
        </div>
      </div>
      <ol class="list-decimal list-inside space-y-2 text-sm text-slate-300">
        <li>Open your <a href="https://login.tailscale.com/admin/machines" target="_blank" class="text-cyan-400 underline hover:text-cyan-300">Tailscale Admin Machines Console</a>.</li>
        <li>Find <strong>argon-host</strong> (100.123.87.122) and click the <strong>⋯</strong> button on the right.</li>
        <li>Click <strong>Share...</strong> and enter the user's email address (or generate a share link).</li>
        <li>When they accept the invite, the Pi appears securely inside their Tailscale app with zero router port forwarding!</li>
        <li>They can then open the <strong>Argon Mobile Hub</strong> app or browser and log in with their credentials.</li>
      </ol>
    </div>
  </main>

  <!-- Add User Modal -->
  <div id="addUserModal" class="hidden fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-800 w-full max-w-md rounded-2xl p-6 shadow-2xl relative">
      <h3 class="text-lg font-bold text-white mb-4">Add New User Profile</h3>
      <form id="addUserForm" class="space-y-4">
        <div>
          <label class="block text-xs font-medium text-slate-300 uppercase mb-1">Username</label>
          <input type="text" id="newUsername" required placeholder="john_doe"
            class="w-full px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-300 uppercase mb-1">Email (Google Account or Email)</label>
          <input type="email" id="newEmail" required placeholder="john@gmail.com"
            class="w-full px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-300 uppercase mb-1">Initial Password</label>
          <input type="password" id="newPassword" required placeholder="Min 6 characters"
            class="w-full px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-300 uppercase mb-1">Role Privilege</label>
          <select id="newRole" class="w-full px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-white focus:outline-none focus:border-cyan-500">
            <option value="user">Standard User (Daily Tools Only)</option>
            <option value="admin">Administrator (Full Homelab Access)</option>
          </select>
        </div>

        <div class="flex items-center justify-end space-x-3 mt-6 pt-4 border-t border-slate-800">
          <button type="button" onclick="closeAddUserModal()" class="px-4 py-2 rounded-xl text-sm text-slate-400 hover:text-white transition">Cancel</button>
          <button type="submit" class="px-4 py-2 rounded-xl text-sm font-medium text-white bg-cyan-600 hover:bg-cyan-500 transition">Save User</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    let currentAdmin = null;

    async function loadData() {
      try {
        const meRes = await fetch('/api/auth/me');
        if (!meRes.ok) throw new Error('Not logged in');
        const meData = await meRes.json();
        currentAdmin = meData.user;
        if (currentAdmin.role !== 'admin') {
          alert('Access denied. Admin role required.');
          window.location.href = '/login';
          return;
        }
        document.getElementById('currentAdminName').textContent = currentAdmin.display_name || currentAdmin.username;

        const usersRes = await fetch('/api/admin/users');
        if (!usersRes.ok) throw new Error('Failed to fetch users');
        const data = await usersRes.json();
        renderUsers(data.users);
      } catch (err) {
        window.location.href = '/login';
      }
    }

    function renderUsers(users) {
      document.getElementById('statTotalUsers').textContent = users.length;
      document.getElementById('statAdmins').textContent = users.filter(u => u.role === 'admin').length;
      document.getElementById('statUsers').textContent = users.filter(u => u.role === 'user').length;

      const tbody = document.getElementById('userTableBody');
      if (users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="px-6 py-6 text-center text-slate-500">No users found.</td></tr>';
        return;
      }

      tbody.innerHTML = users.map(u => {
        const isAdmin = u.role === 'admin';
        const rolePill = isAdmin 
          ? '<span class="px-2.5 py-1 rounded-full text-xs font-semibold bg-purple-500/10 text-purple-400 border border-purple-500/20">ADMIN</span>'
          : '<span class="px-2.5 py-1 rounded-full text-xs font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">USER</span>';
        
        const authBadges = [];
        if (u.has_password) authBadges.push('<span class="text-xs bg-slate-800 text-slate-300 px-2 py-0.5 rounded border border-slate-700">Password</span>');
        if (u.google_id) authBadges.push('<span class="text-xs bg-red-500/10 text-red-400 px-2 py-0.5 rounded border border-red-500/20">Google</span>');

        return `
          <tr class="hover:bg-slate-800/40 transition">
            <td class="px-6 py-4 font-medium text-white flex items-center space-x-2">
              <div class="w-8 h-8 rounded-lg bg-slate-800 border border-slate-700 flex items-center justify-center text-xs font-bold text-slate-300 uppercase">
                ${u.username.substring(0, 2)}
              </div>
              <span>${u.username}</span>
            </td>
            <td class="px-6 py-4 text-slate-300">${u.email}</td>
            <td class="px-6 py-4">${rolePill}</td>
            <td class="px-6 py-4 space-x-1">${authBadges.join(' ') || '<span class="text-xs text-slate-500">None</span>'}</td>
            <td class="px-6 py-4 text-slate-400 text-xs">${u.created_at ? u.created_at.substring(0, 10) : '-'}</td>
            <td class="px-6 py-4 text-right space-x-2">
              <button onclick="toggleRole(${u.id}, '${isAdmin ? 'user' : 'admin'}')"
                class="text-xs px-2.5 py-1 rounded-lg border border-slate-700 hover:bg-slate-800 text-slate-300 transition">
                Set to ${isAdmin ? 'User' : 'Admin'}
              </button>
              ${u.id !== currentAdmin.id ? `
                <button onclick="deleteUser(${u.id}, '${u.username}')"
                  class="text-xs px-2.5 py-1 rounded-lg border border-rose-500/30 text-rose-400 hover:bg-rose-500/10 transition">
                  Delete
                </button>
              ` : ''}
            </td>
          </tr>
        `;
      }).join('');
    }

    async function toggleRole(userId, newRole) {
      if (!confirm(`Are you sure you want to change this user's role to ${newRole.toUpperCase()}?`)) return;
      try {
        const res = await fetch(`/api/admin/users/${userId}/role`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: newRole })
        });
        if (!res.ok) {
          const err = await res.json();
          alert(err.detail || 'Failed to update role');
          return;
        }
        loadData();
      } catch (err) {
        alert(err.message);
      }
    }

    async function deleteUser(userId, username) {
      if (!confirm(`Are you sure you want to permanently delete user "${username}"?`)) return;
      try {
        const res = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
        if (!res.ok) {
          const err = await res.json();
          alert(err.detail || 'Failed to delete user');
          return;
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
      document.getElementById('addUserForm').reset();
    }

    document.getElementById('addUserForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('newUsername').value.trim();
      const email = document.getElementById('newEmail').value.trim();
      const password = document.getElementById('newPassword').value;
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
</html>
""")
