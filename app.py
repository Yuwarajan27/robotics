"""
Runs on the AWS server (NOT on the Pi - no GPIO here).

- Serves the web controller page.
- /ws      -> browsers connect here (the D-pad UI).
- /device  -> the Raspberry Pi connects out to here as a WebSocket client.

This process just relays messages between whichever browsers are connected
and whichever Pi is connected. It also tracks "mode" so a newly-connected
browser can be told the current state immediately.
"""

import threading

from flask import Flask, render_template
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

state_lock = threading.Lock()
mode = "MANUAL"          # last known mode, mirrored from the Pi
device_ws = None         # the single connected Raspberry Pi, or None
browsers = set()         # all connected browser sockets


def send_safe(ws, message):
    """Send with a guard so one dead socket doesn't crash the relay loop."""
    try:
        ws.send(message)
    except Exception:
        pass


def broadcast_to_browsers(message):
    with state_lock:
        targets = list(browsers)
    for b in targets:
        send_safe(b, message)


@app.route('/')
def index():
    return render_template('index.html')


# ----------------------------------------------------------------------
# Browser <-> server
# ----------------------------------------------------------------------
@sock.route('/ws')
def browser_socket(ws):
    global mode
    with state_lock:
        browsers.add(ws)
        current_mode = mode
        device_connected = device_ws is not None
    send_safe(ws, f"MODE:{current_mode}")
    if not device_connected:
        send_safe(ws, "ERR:rover is offline (Pi not connected)")

    print("[BROWSER] connected")
    try:
        while True:
            message = ws.receive()
            if not message:
                break

            with state_lock:
                target = device_ws

            if target is None:
                send_safe(ws, "ERR:rover is offline (Pi not connected)")
                continue

            # forward the raw command (F/B/L/R/S or MODE:...) straight to the Pi
            send_safe(target, message)
    finally:
        with state_lock:
            browsers.discard(ws)
        print("[BROWSER] disconnected")


# ----------------------------------------------------------------------
# Raspberry Pi <-> server
# ----------------------------------------------------------------------
@sock.route('/device')
def device_socket(ws):
    global device_ws, mode
    with state_lock:
        device_ws = ws
    print("[DEVICE] Raspberry Pi connected")
    broadcast_to_browsers("SYS:rover connected")

    try:
        while True:
            message = ws.receive()
            if not message:
                break

            # the Pi reports mode changes / ACKs / telemetry - relay to all browsers
            if message.startswith("MODE:") or message.startswith("ACK:MODE:"):
                new_mode = message.split(":")[-1]
                with state_lock:
                    mode = new_mode
            broadcast_to_browsers(message)
    finally:
        with state_lock:
            if device_ws is ws:
                device_ws = None
        print("[DEVICE] Raspberry Pi disconnected")
        broadcast_to_browsers("ERR:rover is offline (Pi not connected)")


if __name__ == '__main__':
    # debug=False: the reloader would spawn a second process and break the
    # single shared device_ws / browsers state.
    app.run(host='0.0.0.0', port=8000, debug=False)
