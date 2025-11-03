import subprocess
import threading
import re
import time
import os
import json
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# =============================
# CONFIG
# =============================
MINECRAFT_DIR = r"path/to/server/root/for/example/server/jar/is/in/root/"
JAVA_PATH = os.path.join(MINECRAFT_DIR, "javbin", "bin", "javaw.exe")  # <-- local java
MAX_CHAT_LINES = 500
WEB_PORT = 5000
# =============================

# Auto-detect server JAR
def find_server_jar():
    for f in os.listdir(MINECRAFT_DIR):
        if f.lower().endswith(".jar"):
            return f
    return None

SERVER_JAR = find_server_jar()
if not SERVER_JAR:
    print(f"ERROR: No .jar found in {MINECRAFT_DIR}")
    exit(1)

START_CMD = [JAVA_PATH, "-Xmx4G", "-jar", SERVER_JAR, "nogui"]

server_process = None
chat_log = []
players_online = set()

# =============================
# HTML FRONTEND (embedded)
# =============================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>WebCraft</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; background:#111; color:#ddd; height:100vh; overflow:hidden; }
.container { display:grid; grid-template-columns: 1fr 250px; grid-template-rows: auto 1fr; height:100vh; }
header { grid-column:1 / 3; background: linear-gradient(to bottom, #222, #111); padding:12px 20px; border-bottom:1px solid #333; display:flex; justify-content:space-between; align-items:center; }
header h1 { font-size:1.8rem; color:#4caf50; text-shadow: 1px 1px #000; }
header p { font-size:0.9rem; }
.main { grid-column:1; display:flex; flex-direction:column; padding:12px; }
.chat { flex:1; overflow-y:auto; padding-right:8px; border:1px solid #333; background:#000; font-family: monospace; font-size:0.95rem; }
.message { margin-bottom:4px; word-break:break-word; }
.input-area { display:flex; margin-top:8px; }
.input-area input { flex:1; padding:8px 12px; background:#222; border:1px solid #444; color:#fff; border-radius:6px 0 0 6px; }
.input-area button { padding:0 16px; background: linear-gradient(to bottom, #66cc66, #4caf50); color:#fff; border:none; border-radius:0 6px 6px 0; cursor:pointer; box-shadow: 0 2px 3px rgba(0,0,0,0.4); }
.input-area button:hover { background: linear-gradient(to bottom, #77dd77, #57b957); }
.sidebar { background:#1a1a1a; border-left:1px solid #333; padding:12px; overflow-y:auto; }
.sidebar h3 { margin-bottom:8px; color:#aaa; font-size:0.95rem; }
.player { margin-bottom:6px; font-size:0.9rem; }
.chat::-webkit-scrollbar, .sidebar::-webkit-scrollbar { width:8px; }
.chat::-webkit-scrollbar-track, .sidebar::-webkit-scrollbar-track { background:#222; }
.chat::-webkit-scrollbar-thumb { background:#444; border-radius:4px; }
</style>
</head>
<body>
  <div class="container">
    <header>
      <h1>WebCraft</h1>
      <p>Guest: <strong>{{ user_prefix }}</strong></p>
    </header>
    <div class="main">
      <div class="chat" id="chat"></div>
      <div class="input-area">
        <input type="text" id="msgInput" placeholder="Type a message..." maxlength="200" autocomplete="off"/>
        <button onclick="sendMessage()">Send</button>
      </div>
    </div>
    <div class="sidebar">
      <h3>Players Online</h3>
      <div id="players"><em>No players yet</em></div>
    </div>
  </div>

<script>
async function fetchData() {
  const res = await fetch('/chat');
  const data = await res.json();
  const chatDiv = document.getElementById('chat');
  chatDiv.innerHTML = data.messages.map(line => '<div class="message">' + line + '</div>').join('');
  chatDiv.scrollTop = chatDiv.scrollHeight;

  const players = data.players;
  const pDiv = document.getElementById('players');
  pDiv.innerHTML = players.length
    ? players.map(p => '<div class="player">' + p + '</div>').join('')
    : '<em>No players online</em>';
}

async function sendMessage() {
  const input = document.getElementById('msgInput');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  await fetch('/send', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({msg})
  });
}

setInterval(fetchData, 1000);
fetchData();
</script>
</body>
</html>
"""

# =============================
# SERVER MANAGEMENT
# =============================
def start_server():
    global server_process
    if not os.path.isfile(JAVA_PATH):
        print(f"ERROR: javaw.exe not found at {JAVA_PATH}")
        exit(1)

    server_process = subprocess.Popen(
        START_CMD,
        cwd=MINECRAFT_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True
    )
    threading.Thread(target=read_server_output, daemon=True).start()


def read_server_output():
    global chat_log, players_online
    chat_pattern = re.compile(r"\[.*?\]: <([^>]+)> (.*)")
    join_pattern = re.compile(r"\[.*?\]: (\w+) joined the game")
    leave_pattern = re.compile(r"\[.*?\]: (\w+) left the game")

    for line in server_process.stdout:
        line = line.strip()
        if not line:
            continue
        print(line)

        chat_match = chat_pattern.search(line)
        join_match = join_pattern.search(line)
        leave_match = leave_pattern.search(line)

        if chat_match:
            user, msg = chat_match.groups()
            chat_log.append(f"&lt;{user}&gt; {msg}")
        elif join_match:
            user = join_match.group(1)
            players_online.add(user)
            chat_log.append(f"?? {user} joined the game")
        elif leave_match:
            user = leave_match.group(1)
            players_online.discard(user)
            chat_log.append(f"?? {user} left the game")

        if len(chat_log) > MAX_CHAT_LINES:
            chat_log = chat_log[-MAX_CHAT_LINES:]


def monitor_restart():
    while True:
        if server_process and server_process.poll() is not None:
            print("Server crashed. Restarting in 5 seconds...")
            time.sleep(5)
            start_server()
        time.sleep(3)


# =============================
# POLL /list every minute
# =============================
def poll_minecraft_players():
    """Poll Minecraft /list command every 60s to refresh players_online"""
    list_pattern = re.compile(r"There are \d+ of a max \d+: (.*)")
    while True:
        if server_process and server_process.poll() is None:
            try:
                server_process.stdin.write("/list\n")
                server_process.stdin.flush()
                
                # Read lines for next 2 seconds to capture output
                start = time.time()
                buffer = []
                while time.time() - start < 2:
                    line = server_process.stdout.readline()
                    if not line:
                        break
                    line = line.strip()
                    buffer.append(line)
                
                # Parse the line containing players
                for line in buffer:
                    match = list_pattern.search(line)
                    if match:
                        players = [p.strip() for p in match.group(1).split(",") if p.strip()]
                        # Keep website guests but update in-game players
                        website_guests = {p for p in players_online if p.startswith("WebGuest")}
                        players_online.clear()
                        players_online.update(website_guests)
                        players_online.update(players)
            except Exception as e:
                print("Error polling /list:", e)
        time.sleep(60)


# =============================
# FLASK ROUTES
# =============================
@app.route("/")
def index():
    client_ip = request.remote_addr or "0.0.0.0"
    first_octet = client_ip.split(".")[0]
    digits = first_octet[:2].zfill(2)
    user_prefix = f"WebGuest{digits}"
    return render_template_string(HTML_PAGE, user_prefix=user_prefix)


@app.route("/chat")
def chat():
    return jsonify({"messages": chat_log[-200:], "players": list(players_online)})


@app.route("/send", methods=["POST"])
def send():
    data = request.get_json()
    msg = data.get("msg", "").strip()
    if not msg:
        return jsonify({"success": False})

    if not server_process or server_process.poll() is not None:
        return jsonify({"success": False, "error": "Server not running"})

    client_ip = request.remote_addr or "0.0.0.0"
    first_octet = client_ip.split(".")[0]
    digits = first_octet[:2].zfill(2)
    user_prefix = f"WebGuest{digits}"

    try:
        payload = json.dumps({"text": f"<{user_prefix}> {msg}"})
        tellraw_cmd = f"/tellraw @a {payload}\n"
        server_process.stdin.write(tellraw_cmd)
        server_process.stdin.flush()

        chat_log.append(f"&lt;{user_prefix}&gt; {msg}")
        players_online.add(user_prefix)  # track website guest
        return jsonify({"success": True})
    except Exception as e:
        print("Error sending:", e)
        return jsonify({"success": False, "error": str(e)})


# =============================
# MAIN
# =============================
if __name__ == "__main__":
    if not os.path.isdir(MINECRAFT_DIR):
        print(f"ERROR: Minecraft directory does not exist: {MINECRAFT_DIR}")
        exit(1)

    print(f"Launching Minecraft server from {SERVER_JAR} using {JAVA_PATH}...")
    start_server()
    threading.Thread(target=monitor_restart, daemon=True).start()
    threading.Thread(target=poll_minecraft_players, daemon=True).start()
    print(f"Web frontend available at http://127.0.0.1:{WEB_PORT}")
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False)
