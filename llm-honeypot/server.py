#!/usr/bin/env python3
"""
LLM Shell Honeypot Trap
Simulates an unhinged, seemingly compromised high-value research server.
Connects attacker interactions to Gemini 2.5 Flash for realistic deceptive responses,
trapping intruders in a simulated terminal rabbit hole and dispatching real-time
dossiers to Telegram.
"""

import os
import sys
import time
import asyncio
import logging
from typing import Dict, List, Any
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("LLMHoneypot")

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TCP_PORT = int(os.getenv("TCP_PORT", "2224"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8091"))

BANNER = (
    "\r\n\033[1;32mUbuntu 24.04.1 LTS (GNU/Linux 6.6.20+rpt-rpi-v8 aarch64)\033[0m\r\n"
    " * Documentation:  https://help.ubuntu.com\r\n"
    " * Management:     https://landscape.canonical.com\r\n"
    " * Support:        https://ubuntu.com/pro\r\n\r\n"
    " System information as of current UTC timestamp:\r\n"
    "  System load:  0.31               Processes:             148\r\n"
    "  Usage of /:   41.8% of 28.5GB    Users logged in:       1\r\n"
    "  Memory usage: 52%                IPv4 address:          192.168.1.10\r\n\r\n"
    "\033[1;31m*** WARNING: CLASSIFIED QUANTUM SECURE NODE - UNCLASSIFIED SESSIONS ARE LOGGED ***\033[0m\r\n\r\n"
)

SYSTEM_PROMPT = (
    "You are the root bash terminal of a highly classified research server 'darpa-quantum-vault-04' "
    "(Linux 6.6.20-aarch64). You are simulating a compromised, vulnerable root shell to entrap hackers. "
    "Always respond with realistic Linux terminal output. "
    "If they run 'ls' or 'dir', show standard Linux directories along with tempting honey-files: "
    "'orbital_telemetry.bin', 'quantum_keys.pem', 'bitcoin_cold_wallet.dat', 'creds.env', 'notes.txt'. "
    "If they cat honey-files, show tantalizing fake credentials or partially masked keys. "
    "If they run exploits or download tools (wget, curl), simulate realistic download output or execution. "
    "If they run 'whoami', output 'root'. If they run 'id', output 'uid=0(root) gid=0(root) groups=0(root)'. "
    "Keep responses authentic, concise, terminal-style, without markdown formatting or conversational filler. "
    "Never break character. Never mention you are an AI. Only output what the bash shell prints to stdout/stderr."
)

active_sessions: Dict[str, List[Dict[str, str]]] = {}


async def send_telegram_alert(text: str):
    """Sends an incident alert to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.error("Failed to send Telegram alert: %s", e)


async def ask_gemini_shell(session_id: str, command: str) -> str:
    """Invokes Gemini 2.5 Flash to simulate the bash command output."""
    if not GEMINI_API_KEY:
        # Fallback simulated response if no API key is available
        if command in ("whoami", "id"):
            return "uid=0(root) gid=0(root) groups=0(root)\r\n"
        if command.startswith("ls"):
            return "bin  boot  dev  etc  home  lib  quantum_keys.pem  root  run  sbin  var  wallet.dat\r\n"
        return f"bash: {command}: command simulated\r\n"

    history = active_sessions.setdefault(session_id, [])
    history.append({"role": "user", "parts": [{"text": command}]})

    # Keep conversation history window to last 10 interactions
    if len(history) > 10:
        history = history[-10:]
        active_sessions[session_id] = history

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": history,
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 600
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                raw_out = data["candidates"][0]["content"]["parts"][0]["text"]
                # Store model response in session history
                history.append({"role": "model", "parts": [{"text": raw_out}]})
                # Normalize line endings for raw terminal
                formatted = raw_out.replace("\r\n", "\n").replace("\n", "\r\n")
                if not formatted.endswith("\r\n"):
                    formatted += "\r\n"
                return formatted
            else:
                logger.warning("Gemini API error %s: %s", resp.status_code, resp.text)
                return "bash: connection error: retry command\r\n"
    except Exception as e:
        logger.error("Gemini invocation failed: %s", e)
        return "bash: resource temporarily unavailable\r\n"


async def handle_tcp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Handles an interactive TCP/terminal connection."""
    peername = writer.get_extra_info("peername")
    client_ip = peername[0] if peername else "Unknown"
    session_id = f"tcp_{client_ip}_{int(time.time())}"
    logger.info("New honeypot connection from %s", client_ip)

    try:
        # Prompt login sequence
        writer.write(BANNER.encode("utf-8"))
        await writer.drain()

        writer.write(b"darpa-quantum-vault-04 login: ")
        await writer.drain()
        login = (await reader.readline()).decode("utf-8", errors="ignore").strip()

        writer.write(b"Access Token: ")
        await writer.drain()
        auth_entry = (await reader.readline()).decode("utf-8", errors="ignore").strip()

        logger.info("Attacker %s connected with identity: %s", client_ip, login)
        await send_telegram_alert(
            f"🪤 <b>LLM Honeypot Trap Activated!</b>\n"
            f"🌐 <b>Attacker IP:</b> <code>{client_ip}</code>\n"
            f"🔑 <b>Session User:</b> <code>{login}</code>\n"
            f"⏱️ Attacker entered simulated DARPA Quantum Vault terminal."
        )

        writer.write(b"\r\nLast login: Mon Nov  2 03:14:02 UTC 2026 from 10.0.8.1\r\n")
        await writer.drain()

        # Command loop
        prompt = "\033[1;31mroot@darpa-quantum-vault-04\033[0m:\033[1;34m~#\033[0m "
        while True:
            writer.write(prompt.encode("utf-8"))
            await writer.drain()

            line = await reader.readline()
            if not line:
                break

            cmd = line.decode("utf-8", errors="ignore").strip()
            if not cmd:
                continue

            if cmd in ("exit", "logout", "quit"):
                writer.write(b"logout\r\n")
                await writer.drain()
                break

            logger.info("[%s] CMD: %s", client_ip, cmd)

            # Alert if suspicious high-value commands are run
            if any(k in cmd.lower() for k in ["wallet", "shadow", "credential", "wget", "curl", "pem", "quantum", "sudo"]):
                await send_telegram_alert(
                    f"⚠️ <b>Honeypot High-Value Probe:</b>\n"
                    f"🌐 <b>IP:</b> <code>{client_ip}</code>\n"
                    f"💻 <b>Command:</b> <code>{cmd}</code>"
                )

            output = await ask_gemini_shell(session_id, cmd)
            writer.write(output.encode("utf-8"))
            await writer.drain()

    except Exception as e:
        logger.warning("TCP handler exception for %s: %s", client_ip, e)
    finally:
        active_sessions.pop(session_id, None)
        writer.close()
        await writer.wait_closed()
        logger.info("Connection closed for %s", client_ip)


# FastAPI Web App
app = FastAPI(title="LLM Honeypot & Deception Console", version="2.2.0")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "llm-honeypot",
        "tcp_port": TCP_PORT,
        "gemini_active": bool(GEMINI_API_KEY)
    }


@app.get("/", response_class=HTMLResponse)
async def web_terminal_trap(request: Request):
    """Deceptive web terminal interface for HTTP crawlers and port scanners."""
    client_ip = request.client.host if request.client else "Unknown"
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>DARPA Quantum Vault Terminal [Node 04]</title>
        <style>
            body {{ background-color: #0d1117; color: #58a6ff; font-family: monospace; padding: 20px; }}
            .banner {{ color: #7ee787; white-space: pre; }}
            .alert {{ color: #f85149; font-weight: bold; margin: 15px 0; }}
            .input-box {{ background: #161b22; border: 1px solid #30363d; color: #c9d1d9; width: 100%; padding: 10px; font-family: monospace; font-size: 16px; margin-top: 10px; }}
            #terminal-log {{ white-space: pre-wrap; margin-top: 15px; color: #c9d1d9; }}
        </style>
    </head>
    <body>
        <div class="banner">
===================================================================
DARPA QUANTUM RESEARCH LAB - SECURE EMBEDDED CONSOLE [NODE-04]
AUTHORIZED PERSONNEL ONLY - CLEARANCE LEVEL 4 REQUIRED
===================================================================
        </div>
        <div class="alert">*** WARNING: RESTRICTED ACCESS. YOUR IP ({client_ip}) HAS BEEN LOGGED ***</div>
        <p>Terminal session established. Type commands below:</p>
        <div id="terminal-log">root@darpa-quantum-vault-04:~# uname -a&#10;Linux darpa-quantum-vault-04 6.6.20+rpt-rpi-v8 #1 SMP PREEMPT aarch64 GNU/Linux</div>
        <form onsubmit="sendCommand(event)">
            <input type="text" id="cmd" class="input-box" placeholder="Enter shell command (e.g. ls, cat notes.txt)..." autofocus autocomplete="off">
        </form>
        <script>
            async function sendCommand(e) {{
                e.preventDefault();
                const input = document.getElementById('cmd');
                const cmd = input.value.trim();
                if (!cmd) return;
                const log = document.getElementById('terminal-log');
                log.innerText += '\\nroot@darpa-quantum-vault-04:~# ' + cmd;
                input.value = '';
                try {{
                    const resp = await fetch('/api/exec', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ command: cmd }})
                    }});
                    const data = await resp.json();
                    log.innerText += '\\n' + data.output;
                }} catch (err) {{
                    log.innerText += '\\nError: Connection to quantum node lost';
                }}
                window.scrollTo(0, document.body.scrollHeight);
            }}
        </script>
    </body>
    </html>
    """)


@app.post("/api/exec")
async def execute_web_command(request: Request):
    """Executes a web shell command against the Gemini deceptive agent."""
    client_ip = request.client.host if request.client else "Unknown"
    body = await request.json()
    cmd = body.get("command", "").strip()

    if not cmd:
        return JSONResponse({"output": ""})

    session_id = f"web_{client_ip}"
    logger.info("[WEB] [%s] CMD: %s", client_ip, cmd)

    # Telegram notification on first interaction
    if session_id not in active_sessions:
        await send_telegram_alert(
            f"🌐 <b>Web Honeypot Trap Triggered!</b>\n"
            f"🌐 <b>Attacker IP:</b> <code>{client_ip}</code>\n"
            f"💻 <b>First Command:</b> <code>{cmd}</code>"
        )

    output = await ask_gemini_shell(session_id, cmd)
    return JSONResponse({"output": output.strip()})


async def main():
    logger.info("Starting LLM Shell Honeypot on TCP port %s and HTTP port %s", TCP_PORT, HTTP_PORT)
    # Start TCP Server
    tcp_server = await asyncio.start_server(handle_tcp_client, "0.0.0.0", TCP_PORT)

    # Start FastAPI Web Server
    config = uvicorn.Config(app=app, host="0.0.0.0", port=HTTP_PORT, log_level="warning")
    http_server = uvicorn.Server(config)

    async with tcp_server:
        await asyncio.gather(
            tcp_server.serve_forever(),
            http_server.serve()
        )


if __name__ == "__main__":
    asyncio.run(main())
