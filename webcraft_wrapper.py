import subprocess
import threading
import re
import time
import os
import json
import datetime
import signal
import atexit
from flask import Flask, request, jsonify, render_template_string, session
from werkzeug.utils import secure_filename
import shutil
import uuid
import requests # NEW: Import for downloading files

# -----------------------------
# CONFIG
# -----------------------------
MINECRAFT_DIR = r"path\to\server\root"
JAVA_PATH = os.path.join(MINECRAFT_DIR, "javbin", "bin", "javaw.exe")
BACKUP_DIR = r"path\to\backup\folder"
MAX_CHAT_LINES = 500
WEB_PORT = 5000
WINRAR_PATH = r"C:\Program Files\WinRAR\Rar.exe"
PASSFILE = os.path.join(os.path.dirname(__file__), "passkey.txt")
UPLOAD_TMP_DIR = os.path.join(os.path.dirname(__file__), "tmp_uploads")

# NEW: Geyser specific configuration
GEYSER_JAR_PATH = os.path.join(MINECRAFT_DIR, "mods", "Geyser-Fabric.jar")
GEYSER_DOWNLOAD_URL = "https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/fabric"
GEYSER_UPDATE_PATTERN = re.compile(r"here's a new Geyser update available to support Bedrock version \S+\. Download it here: \|")


os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(UPLOAD_TMP_DIR, exist_ok=True)
os.makedirs(os.path.join(MINECRAFT_DIR, "mods"), exist_ok=True) # Ensure mods folder exists

if not os.path.exists(PASSFILE):
    with open(PASSFILE, "w", encoding="utf-8") as f:
        f.write("")

# -----------------------------
# Find server jar
# -----------------------------
def find_server_jar():
    for f in os.listdir(MINECRAFT_DIR):
        if f.lower().endswith(".jar") and "server" in f.lower():
            return f
    for f in os.listdir(MINECRAFT_DIR):
        if f.lower().endswith(".jar"):
            return f
    return None

SERVER_JAR = find_server_jar()
if not SERVER_JAR:
    print(f"ERROR: No .jar found in {MINECRAFT_DIR}")
    exit(1)

START_CMD = [JAVA_PATH, "-Xmx4G", "-jar", SERVER_JAR, "nogui"]

# -----------------------------
# Globals
# -----------------------------
app = Flask(__name__)
app.secret_key = os.urandom(24)

server_process = None
chat_log = []
players_online = set()
shutdown_flag = False
server_output_buffer = []
SERVER_OUTPUT_MAX = 2000

task_lock = threading.Lock()
current_task = None
task_started_at = None

# Crash detection patterns — only backup on real crash
CRASH_PATTERNS = [
    re.compile(r"Exception in server tick loop", re.IGNORECASE),
    re.compile(r"java\.lang\.OutOfMemoryError", re.IGNORECASE),
    re.compile(r"Crash report", re.IGNORECASE),
    re.compile(r"FATAL", re.IGNORECASE),
    re.compile(r"Severe", re.IGNORECASE),
    re.compile(r"Could not pass event", re.IGNORECASE),
    re.compile(r"Failed to handle packet", re.IGNORECASE),
    re.compile(r"Watchdog", re.IGNORECASE),
    re.compile(r"starlight", re.IGNORECASE),  # Starlight crashes
]

# NEW: Global to track last scheduled backup time
last_scheduled_backup = None

def start_task(name):
    with task_lock:
        global current_task, task_started_at
        
        if current_task is not None:
            return False
            
        current_task = name
        task_started_at = time.time()
        print(f"[TASK] Started: {name}")
        return True

def finish_task():
    with task_lock:
        global current_task, task_started_at
        current_task = None
        task_started_at = None
        print(f"[TASK] Finished.")

def is_task_running():
    with task_lock:
        return current_task is not None

# -----------------------------
# Clean exit
# -----------------------------
def kill_server():
    global server_process, shutdown_flag
    if shutdown_flag:
        return
    shutdown_flag = True
    print("\n[EXIT] Shutting down - stopping server...")
    if server_process and server_process.poll() is None:
        try:
            # Send stop command if process is running and hasn't closed stdin/out
            if server_process.stdin:
                server_process.stdin.write("/stop\n")
                server_process.stdin.flush()
            time.sleep(3)
            if server_process.poll() is None:
                server_process.terminate()
                time.sleep(2)
            if server_process.poll() is None:
                server_process.kill()
        except:
            pass
    print("[EXIT] Server stopped.")

atexit.register(kill_server)
signal.signal(signal.SIGINT, lambda s, f: (kill_server(), os._exit(0)))
signal.signal(signal.SIGTERM, lambda s, f: (kill_server(), os._exit(0)))

# -----------------------------
# FULL HTML TEMPLATES (Unchanged)
# -----------------------------
# ... (HTML_PAGE and ADMIN_PAGE remain here as they were)
HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>WebCraft</title>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
:root { --bg:#0f1113; --panel:#151719; --muted:#9aa1a7; --accent:#1982c4; --good:#2ea44f; --bad:#d73a49; color-scheme: dark; }
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:#e6eef6;font-family:Segoe UI,Roboto,Arial,sans-serif}
.header{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;background:var(--panel);border-bottom:1px solid rgba(255,255,255,0.03)}
.title{display:flex;gap:12px;align-items:center}
.h1{font-size:18px;margin:0}
.sub{color:var(--muted);font-size:13px}
.layout{display:grid;grid-template-columns:1fr 320px;height:calc(100vh - 66px)}
.main{padding:12px;display:flex;flex-direction:column;gap:10px}
.chat{flex:1;background:#0b0c0d;border:1px solid rgba(255,255,255,0.03);padding:10px;overflow:auto;font-family:monospace;font-size:13px}
.input{display:flex;gap:8px}
.input input{flex:1;padding:10px;background:var(--panel);border:1px solid rgba(255,255,255,0.03);color:inherit}
.btn{background:var(--accent);color:#fff;border:none;padding:8px 12px;border-radius:6px;cursor:pointer}
.sidebar{background:var(--panel);padding:12px;border-left:1px solid rgba(255,255,255,0.03);overflow:auto}
.section{margin-bottom:12px}
.players{background:#060607;padding:8px;border:1px solid rgba(255,255,255,0.02);min-height:40px}
.small{font-size:12px;color:var(--muted)}
.admin-btn{background:#0b9bd7;padding:6px 10px;border-radius:6px;border:none;color:#fff;cursor:pointer}
.bad{background:var(--bad)}
.good{background:var(--good)}
.gray{background:#2b2f33}
.kv{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed rgba(255,255,255,0.02)}
</style>
</head>
<body>
<div class="header">
  <div class="title">
    <div>
      <h1 class="h1">WebCraft</h1>
      <div class="sub">Guest: <strong>{{ user_prefix }}</strong></div>
    </div>
  </div>
  <div>
    <button class="admin-btn" onclick="window.open('/admin','_blank')">Open Admin</button>
  </div>
</div>
<div class="layout">
  <div class="main">
    <div class="chat" id="chat"></div>
    <div class="input">
      <input id="msgInput" placeholder="Type message to broadcast to players..." />
      <button class="btn" onclick="sendMessage()">Send</button>
    </div>
  </div>
  <div class="sidebar">
    <div class="section">
      <h3 style="margin:0 0 8px 0">Players Online</h3>
      <div class="players" id="players">No players yet</div>
    </div>
    <div class="section">
      <div class="kv"><div class="small">Server Status</div><div id="srvStatus" class="small">unknown</div></div>
      <div class="kv"><div class="small">Task</div><div id="taskStatus" class="small">idle</div></div>
      <div style="margin-top:8px">
        <button class="admin-btn" onclick="window.open('/admin','_blank')">Admin Panel</button>
      </div>
    </div>
    <div class="section small">
      <div>Recent log: <span id="lastLogTime">-</span></div>
    </div>
  </div>
</div>
<script>
async function fetchData(){
  try {
    const r = await fetch('/chat'); const d = await r.json();
    const chat = document.getElementById('chat');
    chat.innerHTML = '';
    for(const m of d.messages){
      const el = document.createElement('div');
      el.textContent = m;
      chat.appendChild(el);
    }
    chat.scrollTop = chat.scrollHeight;
    const pl = document.getElementById('players');
    if(d.players && d.players.length){
      pl.innerHTML = '';
      for(const p of d.players){
        const div = document.createElement('div');
        div.textContent = p;
        pl.appendChild(div);
      }
    } else {
      pl.innerHTML = 'No players yet';
    }
    const s = await fetch('/admin/status'); const js = await s.json();
    document.getElementById('srvStatus').textContent = js.server_running ? 'running' : 'stopped';
    document.getElementById('taskStatus').textContent = js.current_task || 'idle';
    if(js.last_log_time) document.getElementById('lastLogTime').textContent = js.last_log_time;
  } catch(e){
    console.error(e);
  }
}
async function sendMessage(){
  const v = document.getElementById('msgInput').value.trim();
  if(!v) return;
  document.getElementById('msgInput').value = '';
  await fetch('/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({msg:v})});
}
setInterval(fetchData, 1000);
fetchData();
</script>
</body>
</html>
"""

ADMIN_PAGE = """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"/><title>Admin - WebCraft</title>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
body{background:#07080a;color:#e6eef6;font-family:Segoe UI,Arial,sans-serif;padding:18px}
.container{max-width:980px;margin:0 auto}
.header{display:flex;justify-content:space-between;align-items:center}
.h1{margin:0}
.small{color:#9aa1a7;font-size:13px}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.btn{background:#1982c4;border:none;color:#fff;padding:8px 12px;border-radius:6px;cursor:pointer}
.btn.gray{background:#2b2f33}
.btn.red{background:#d73a49}
.panel{background:#0f1113;padding:12px;border-radius:8px;margin-top:14px;border:1px solid rgba(255,255,255,0.03)}
.file{display:inline-block}
.input{padding:8px;border-radius:6px;border:1px solid rgba(255,255,255,0.04);background:#0b0c0d;color:inherit}
.status{margin-top:8px}
.logarea{height:260px;overflow:auto;background:#020203;padding:8px;border-radius:6px;border:1px solid rgba(255,255,255,0.02);font-family:monospace;font-size:12px}
.disabled{opacity:0.5;pointer-events:none}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h2 class="h1">Admin Panel</h2>
      <div class="small">Manage server backups, restart, and restores. No password change here (edit passkey.txt).</div>
    </div>
    <div>
      <button class="btn gray" onclick="window.open('/','_blank')">Open Main Panel</button>
    </div>
  </div>
  <div id="authBlock" class="panel">
    <div id="notAuthed">
      <p class="small">Enter admin password (passkey.txt). Blank file = empty password allowed.</p>
      <input id="pw" class="input" type="password" />
      <button class="btn" onclick="doAuth()">Login</button>
      <div id="authMsg" class="small"></div>
    </div>
    <div id="authed" style="display:none">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <div class="small">Authenticated as admin</div>
          <div id="taskIndicator" class="small">Task: idle</div>
        </div>
        <div>
          <button class="btn" onclick="doLogout()">Logout</button>
        </div>
      </div>
      <div class="panel" style="margin-top:10px">
        <h3 style="margin:0 0 8px 0">Server Actions</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button id="btnRestart" class="btn" onclick="startAction('restart')">Restart Server</button>
          <button id="btnStop" class="btn gray" onclick="startAction('stop')">Stop Server</button>
          <button id="btnKill" class="btn red" onclick="startAction('kill')">Kill Server</button>
          <button id="btnBackup" class="btn" onclick="startAction('backup')">Create Backup</button>
          <label class="file">
            <input id="rarfile" type="file" accept=".rar" />
          </label>
          <button id="btnRestore" class="btn" onclick="doRestore()">Restore Backup (upload .rar)</button>
        </div>
      </div>
      <div class="panel" style="margin-top:10px">
        <h3 style="margin:0 0 8px 0">Run Custom Command</h3>
        <div style="display:flex;gap:8px;align-items:center">
            <input id="cmdInput" class="input" placeholder="/say Hello World" style="flex:1" />
            <button class="btn" onclick="sendCommand()">Run</button>
        </div>
        <div class="status" id="statusText"></div>
      </div>
      <div class="panel" style="margin-top:10px">
        <h3 style="margin:0 0 8px 0">Recent server output</h3>
        <div id="logArea" class="logarea"></div>
      </div>
    </div>
  </div>
</div>
<script>
async function doAuth(){
  const pw = document.getElementById('pw').value;
  const r = await fetch('/admin/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pw})});
  if(r.status===200){
    const j = await r.json();
    if(j.ok){ showAuthed(); return; }
  }
  document.getElementById('authMsg').textContent = 'Bad password';
}
async function doLogout(){
  await fetch('/admin/logout',{method:'POST'});
  document.getElementById('authed').style.display='none';
  document.getElementById('notAuthed').style.display='block';
}
function showAuthed(){
  document.getElementById('notAuthed').style.display='none';
  document.getElementById('authed').style.display='block';
  fetchStatus(); updateLogs();
  setInterval(fetchStatus,2000);
  setInterval(updateLogs,2000);
}
async function fetchStatus(){
  const r = await fetch('/admin/status');
  const j = await r.json();
  document.getElementById('taskIndicator').textContent = 'Task: ' + (j.current_task||'idle');
  document.getElementById('statusText').textContent = j.message || '';
  const running = !!j.current_task;
  // Disable main action buttons while a task is running
  const btns = ['btnRestart','btnStop','btnKill','btnBackup','btnRestore'];
  btns.forEach(id=>{ const el=document.getElementById(id); if(el) el.classList.toggle('disabled', running); });
}
async function startAction(action){
  const r = await fetch('/admin/' + action, {method:'POST'});
  const j = await r.json();
  document.getElementById('statusText').textContent = j.message || JSON.stringify(j);
}
// Function to send custom command
async function sendCommand(){
  const cmd = document.getElementById('cmdInput').value.trim();
  if(!cmd) return;
  document.getElementById('cmdInput').value = ''; // Clear input immediately
  
  const r = await fetch('/admin/command', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cmd: cmd})
  });
  const j = await r.json();
  document.getElementById('statusText').textContent = j.message || JSON.stringify(j);
}
async function doRestore(){
  const f = document.getElementById('rarfile').files[0];
  if(!f){ alert('Select a .rar file first'); return; }
  const fd = new FormData();
  fd.append('rarfile', f);
  const r = await fetch('/admin/restore',{method:'POST', body: fd});
  const j = await r.json();
  document.getElementById('statusText').textContent = j.message || JSON.stringify(j);
}
async function updateLogs(){
  const r = await fetch('/admin/logs');
  const j = await r.json();
  const area = document.getElementById('logArea');
  area.innerHTML = '';
  for(const l of j.lines){ const el=document.createElement('div'); el.textContent = l; area.appendChild(el); }
  area.scrollTop = area.scrollHeight;
}
</script>
</body>
</html>
"""

# -----------------------------
# Geyser Update Logic
# -----------------------------
def do_geyser_update_task():
    if not start_task("geyser_update"): return
    print("[GEYSER] Starting Geyser update process...")
    try:
        # 1. Stop Server
        print("[GEYSER] Stopping server for update...")
        send_server_cmd("/stop")
        for _ in range(80):
            if not server_process or server_process.poll() is not None: break
            time.sleep(0.5)
        if server_process and server_process.poll() is None:
            server_process.kill()

        # 2. Delete old jar
        print(f"[GEYSER] Deleting old jar: {GEYSER_JAR_PATH}")
        if os.path.exists(GEYSER_JAR_PATH):
            os.remove(GEYSER_JAR_PATH)
        else:
            print("[GEYSER] Old jar not found, continuing with download.")

        # 3. Download new jar
        print(f"[GEYSER] Downloading new Geyser-Fabric.jar from: {GEYSER_DOWNLOAD_URL}")
        response = requests.get(GEYSER_DOWNLOAD_URL, stream=True)
        response.raise_for_status() # Raise an exception for bad status codes

        with open(GEYSER_JAR_PATH, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        print(f"[GEYSER] Download complete. New jar saved to: {GEYSER_JAR_PATH}")

    except Exception as e:
        print("[GEYSER] Error during update:", e)
    finally:
        # 4. Restart Server
        print("[GEYSER] Restarting server.")
        start_server()
        finish_task()

# -----------------------------
# Server control
# -----------------------------
def start_server():
    global server_process
    # Check if any task (including geyser_update) is running
    if is_task_running() and current_task != "restart": # Allow restart task to call start_server
        return
    if server_process and server_process.poll() is None:
        return
    print("[SERVER] Starting Minecraft server...")
    server_process = subprocess.Popen(
        START_CMD,
        cwd=MINECRAFT_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    threading.Thread(target=read_server_output, daemon=True).start()

def read_server_output():
    global server_output_buffer
    
    chat_pat = re.compile(r"\[.*?\]: <([^>]+)> (.*)")
    join_pat = re.compile(r"\[.*?\]: ([\.\w\-\u00C0-\u017F]+) joined the game")
    leave_pat = re.compile(r"\[.*?\]: ([\.\w\-\u00C0-\u017F]+) left the game")

    while True:
        if not server_process or server_process.poll() is not None:
            break
        try:
            line = server_process.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            line = line.rstrip()
            if not line:
                continue

            server_output_buffer.append(line)
            if len(server_output_buffer) > SERVER_OUTPUT_MAX:
                server_output_buffer = server_output_buffer[-SERVER_OUTPUT_MAX:]

            print(line)

            # NEW: Geyser update detection
            if GEYSER_UPDATE_PATTERN.search(line):
                print("[GEYSER DETECTED] Triggering automatic update...")
                threading.Thread(target=do_geyser_update_task, daemon=True).start()
                # Do not process further log actions for this line

            c = chat_pat.search(line)
            j = join_pat.search(line)
            l = leave_pat.search(line)
            if c:
                user, msg = c.groups()
                players_online.add(user)
                chat_log.append(f"<{user}> {msg}")
            elif j:
                user = j.group(1)
                players_online.add(user)
                chat_log.append(f"Joined: {user}")
                # NEW: Run /replay start command for the joining player
                cmd_to_send = f"replay start players {user}"
                send_server_cmd(cmd_to_send)
                print(f"[ACTION] Ran command on join: {cmd_to_send}")
            elif l:
                user = l.group(1)
                players_online.discard(user)
                chat_log.append(f"Left: {user}")

            if len(chat_log) > MAX_CHAT_LINES:
                chat_log[:] = chat_log[-MAX_CHAT_LINES:]

        except Exception as e:
            print("read_server_output error:", e)
            break

def send_server_cmd(cmd):
    try:
        if server_process and server_process.poll() is None:
            # Strip leading forward slash if present, as console commands often don't need it
            cmd = cmd.lstrip("/")
            server_process.stdin.write(cmd + "\n")
            server_process.stdin.flush()
            return True
    except Exception as e:
        print("send_server_cmd error:", e)
    return False

# -----------------------------
# RAR helpers (Unchanged)
# -----------------------------
def do_rar_archive(out_path, source_dir):
    if not os.path.exists(WINRAR_PATH):
        raise FileNotFoundError("WinRAR not found!")
    # NOTE: Changed `os.path.join(source_dir, "*")` to just `source_dir` 
    # and ensured -ep1 is used for clean archive creation.
    # WinRAR should handle the directory contents.
    cmd = [WINRAR_PATH, "a", "-r", "-ep1", out_path, source_dir]
    result = subprocess.run(cmd, cwd=MINECRAFT_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"WinRAR failed: {result.stderr}")

def extract_rar_to_dir(rar_path, dest_dir):
    if not os.path.exists(WINRAR_PATH):
        raise FileNotFoundError("WinRAR not found!")
    cmd = [WINRAR_PATH, "x", "-y", "-o+", rar_path, dest_dir + os.sep]
    result = subprocess.run(cmd, cwd=MINECRAFT_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Extract failed: {result.stderr}")

# -----------------------------
# Tasks (Unchanged, except for calls to start_task and start_server)
# -----------------------------
def do_backup_task():
    if not start_task("backup"): return
    try:
        print("[BACKUP] Starting backup...")
        send_server_cmd("/stop")
        for _ in range(80):
            if not server_process or server_process.poll() is not None: break
            time.sleep(0.5)
        if server_process and server_process.poll() is None:
            server_process.kill()
        
        # Backup only the world directory
        world_dir = os.path.join(MINECRAFT_DIR, "world")
        if not os.path.isdir(world_dir): 
             print("[BACKUP] 'world' directory not found. Skipping.")
             return
             
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"ServerArchive_{timestamp}.rar")
        
        # Do archive on the 'world' folder contents
        world_content_dir = os.path.join(MINECRAFT_DIR, "world")
        do_rar_archive(backup_file, world_content_dir)
        print(f"[BACKUP] Done: {backup_file}")
    except Exception as e:
        print("[BACKUP] Error:", e)
    finally:
        start_server()
        finish_task()

def do_restore_task(rar_path):
    task_name = f"restore:{os.path.basename(rar_path)}"
    if not start_task(task_name): return
    try:
        print(f"[RESTORE] Starting from {rar_path}")
        send_server_cmd("/stop")
        for _ in range(80):
            if not server_process or server_process.poll() is not None: break
            time.sleep(0.5)
        if server_process and server_process.poll() is None:
            server_process.kill()
        world_dir = os.path.join(MINECRAFT_DIR, "world")
        if os.path.exists(world_dir):
            shutil.rmtree(world_dir)
        os.makedirs(world_dir, exist_ok=True)
        extract_rar_to_dir(rar_path, world_dir)
        print("[RESTORE] Done")
    except Exception as e:
        print("[RESTORE] Error:", e)
    finally:
        start_server()
        try: os.remove(rar_path)
        except: pass
        finish_task()

def do_restart_task():
    if not start_task("restart"): return
    try:
        send_server_cmd("/stop")
        for _ in range(80):
            if not server_process or server_process.poll() is not None: break
            time.sleep(0.5)
        start_server()
    finally:
        finish_task()

def do_stop_task():
    if not start_task("stop"): return
    try:
        send_server_cmd("/stop")
    finally:
        finish_task()

def do_kill_task():
    if not start_task("kill"): return
    try:
        if server_process and server_process.poll() is None:
            server_process.kill()
    finally:
        finish_task()

# -----------------------------
# Scheduled Tasks (NEW)
# -----------------------------
def scheduled_task_monitor():
    global last_scheduled_backup
    while True:
        now = datetime.datetime.now()
        
        # Check for Sunday 12:45 PM backup
        # now.weekday() == 6 is Sunday.
        # now.hour == 12 is 12 PM.
        # now.minute == 45 is 45 minutes past the hour.
        if now.weekday() == 6 and now.hour == 12 and now.minute == 45:
            # Prevent multiple backups within the same minute
            if last_scheduled_backup is None or (now - last_scheduled_backup).total_seconds() > 60:
                print("[SCHEDULE] Starting Sunday 12:45 PM backup...")
                if not is_task_running():
                    threading.Thread(target=do_backup_task, daemon=True).start()
                    last_scheduled_backup = now
                else:
                    print(f"[SCHEDULE] Skipping backup, task '{current_task}' is already running.")
        
        # Check every 20 seconds, allowing for time drift near the target minute
        time.sleep(20)

# -----------------------------
# Crash monitor (Unchanged)
# -----------------------------
def monitor_server_crash():
    global server_process
    while True:
        time.sleep(3)
        if server_process and server_process.poll() is not None:
            print(f"[MONITOR] Server stopped (code {server_process.poll()})")
            recent = "\n".join(server_output_buffer[-80:])
            is_crash = any(p.search(recent) for p in CRASH_PATTERNS)
            if is_crash:
                print("[MONITOR] CRASH DETECTED → Emergency backup")
                if not is_task_running():
                    threading.Thread(target=do_backup_task, daemon=True).start()
                else:
                    print(f"[MONITOR] Cannot start emergency backup, task '{current_task}' is already running.")
            else:
                print("[MONITOR] Normal shutdown — no backup")
            server_process = None

# -----------------------------
# Flask routes (Unchanged)
# -----------------------------
def read_passfile():
    try:
        with open(PASSFILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return ""

# Public Routes
@app.route("/")
def index():
    ip = request.remote_addr or "0.0.0.0"
    prefix = ip.split(".")[0][-2:].zfill(2)
    return render_template_string(HTML_PAGE, user_prefix=f"WebGuest{prefix}")

@app.route("/chat")
def chat():
    return jsonify({"messages": chat_log[-200:], "players": sorted(players_online)})

@app.route("/send", methods=["POST"])
def send():
    msg = request.get_json().get("msg", "").strip()
    if not msg: return jsonify({"success": False})
    
    ip = request.remote_addr or "0.0.0.0"
    user = f"WebGuest{ip.split('.')[0][-2:].zfill(2)}"
    
    # FIX: Use /tellraw @a with JSON formatting
    message_content = f"<{user}> {msg}"
    json_payload = json.dumps({"text": message_content})
    cmd_to_send = f"tellraw @a {json_payload}"
    
    if send_server_cmd(cmd_to_send):
        # Manually add to chat log since tellraw is not parsed back easily
        chat_log.append(message_content) 
        players_online.add(user)
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Server process not running"})

# Admin Routes
@app.route("/admin")
def admin_page():
    return render_template_string(ADMIN_PAGE)

@app.route("/admin/auth", methods=["POST"])
def admin_auth():
    if request.get_json().get("pw", "") == read_passfile():
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 403

@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})

@app.route("/admin/status")
def admin_status():
    running = server_process and server_process.poll() is None
    return jsonify({
        "server_running": running,
        "current_task": current_task,
        "last_log_time": datetime.datetime.now().strftime("%H:%M:%S"),
        "message": "Task running" if current_task else "Idle"
    })

@app.route("/admin/logs")
def admin_logs():
    return jsonify({"lines": server_output_buffer[-400:] + ["--- CHAT ---"] + chat_log[-100:]})

@app.route("/admin/command", methods=["POST"])
def admin_command():
    if not session.get("admin"): return jsonify({"message": "auth required"}), 403
    cmd = request.get_json().get("cmd", "").strip()
    if not cmd:
        return jsonify({"message": "No command provided"}), 400
    
    # Note: send_server_cmd strips the leading slash, which is fine.
    success = send_server_cmd(cmd)
    
    if success:
        return jsonify({"message": f"Command sent: {cmd}"})
    else:
        return jsonify({"message": "Failed to send command. Is the server running?"}), 500

@app.route("/admin/backup", methods=["POST"])
def admin_backup():
    if not session.get("admin"): return jsonify({"message": "auth required"}), 403
    if is_task_running(): return jsonify({"message": "task running"}), 409
    threading.Thread(target=do_backup_task, daemon=True).start()
    return jsonify({"message": "backup started"})

@app.route("/admin/restart", methods=["POST"])
def admin_restart():
    if not session.get("admin"): return jsonify({"message": "auth required"}), 403
    if is_task_running(): return jsonify({"message": "task running"}), 409
    threading.Thread(target=do_restart_task, daemon=True).start()
    return jsonify({"message": "restart started"})

@app.route("/admin/stop", methods=["POST"])
def admin_stop():
    if not session.get("admin"): return jsonify({"message": "auth required"}), 403
    threading.Thread(target=do_stop_task, daemon=True).start()
    return jsonify({"message": "stop command sent"})

@app.route("/admin/kill", methods=["POST"])
def admin_kill():
    if not session.get("admin"): return jsonify({"message": "auth required"}), 403
    threading.Thread(target=do_kill_task, daemon=True).start()
    return jsonify({"message": "kill command sent"})

@app.route("/admin/restore", methods=["POST"])
def admin_restore():
    if not session.get("admin"): return jsonify({"message": "auth required"}), 403
    if "rarfile" not in request.files: return jsonify({"message": "no file"}), 400
    f = request.files["rarfile"]
    if not f.filename.lower().endswith(".rar"): return jsonify({"message": "only .rar"}), 400
    path = os.path.join(UPLOAD_TMP_DIR, f"{uuid.uuid4().hex}_{secure_filename(f.filename)}")
    f.save(path)
    threading.Thread(target=do_restore_task, args=(path,), daemon=True).start()
    return jsonify({"message": "restore started"})

# -----------------------------
# Start everything
# -----------------------------
if __name__ == "__main__":
    print("[MAIN] Starting WebCraft Manager...")
    start_server()
    threading.Thread(target=monitor_server_crash, daemon=True).start()
    threading.Thread(target=scheduled_task_monitor, daemon=True).start() # NEW: Start scheduled task monitor
    print(f"[MAIN] Open: http://127.0.0.1:{WEB_PORT}")
    print(f"[MAIN] Admin: http://127.0.0.1:{WEB_PORT}/admin")
    app.run(host="0.0.0.0", port=WEB_PORT, threaded=True)
