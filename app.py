from flask import Flask, render_template
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

# --- Robot Motor Control Functions ---
def forward():    print("Moving forward")
def backward():   print("Moving backward")
def turn_left():  print("Turning left")
def turn_right(): print("Turning right")
def stop():       print("Stopping")

COMMANDS = {"F": forward, "B": backward, "L": turn_left, "R": turn_right, "S": stop}

# Route to serve your HTML page
@app.route('/')
def index():
    return render_template('index.html')

# WebSocket endpoint integrated into the same server
@sock.route('/ws')
def chatbot(ws):
    print("Client connected via WebSocket")
    while True:
        message = ws.receive()
        if not message:
            break
        
        cmd = message.strip().upper()
        action = COMMANDS.get(cmd)
        if action:
            action()
            ws.send(f"ACK:{cmd}")
        else:
            ws.send(f"ERR:unknown command {cmd}")

if __name__ == '__main__':
    # Runs the unified server on Port 5000
    app.run(host='0.0.0.0', port=5000, debug=True)
