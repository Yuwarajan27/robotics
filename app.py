"""
Rover control server for Raspberry Pi.
- Serves the web controller page.
- Accepts WebSocket connections at /ws.
- Two modes:
    MANUAL -> D-pad commands (F/B/L/R/S) drive the motors directly.
    AUTO   -> a background thread runs line-following using the IR sensor array.
              manual commands are ignored (rejected) while in AUTO.
"""

import threading
import time

from flask import Flask, render_template
from flask_sock import Sock

try:
    import RPi.GPIO as GPIO
    SIMULATED = False
except (ImportError, RuntimeError):
    # Lets you test the server logic on a laptop without real GPIO hardware.
    print("[WARN] RPi.GPIO not available - running in SIMULATED mode (prints only)")
    SIMULATED = True

app = Flask(__name__)
sock = Sock(app)

# ----------------------------------------------------------------------
# PIN CONFIGURATION - EDIT THESE to match your actual wiring (BCM numbering)
# ----------------------------------------------------------------------
ENA, IN1, IN2 = 12, 5, 6       # Left motor  (PWM, dir, dir)
ENB, IN3, IN4 = 13, 20, 21     # Right motor (PWM, dir, dir)

SENSOR_PINS = [17, 27, 22, 10, 9, 11]   # 6 IR sensors, left to right
SENSOR_WEIGHTS = [-5, -3, -1, 1, 3, 5]

PWM_FREQ = 1000        # Hz
MAX_SPEED = 90         # duty cycle %, 0-100
BASE_SPEED = 60         # cruising duty cycle % for auto mode
MANUAL_SPEED = 70       # duty cycle % for manual F/B/L/R

# PID gains for auto line-following (tune during testing)
KP, KI, KD = 25.0, 0.0, 15.0

# ----------------------------------------------------------------------
# STATE
# ----------------------------------------------------------------------
state_lock = threading.Lock()
mode = "MANUAL"            # "MANUAL" or "AUTO"

auto_stop_event = threading.Event()
auto_thread = None

pwm_left = None
pwm_right = None


# ----------------------------------------------------------------------
# HARDWARE SETUP
# ----------------------------------------------------------------------
def gpio_setup():
    global pwm_left, pwm_right
    if SIMULATED:
        return

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    GPIO.setup(ENA, GPIO.OUT)
    GPIO.setup(ENB, GPIO.OUT)
    GPIO.setup(IN1, GPIO.OUT)
    GPIO.setup(IN2, GPIO.OUT)
    GPIO.setup(IN3, GPIO.OUT)
    GPIO.setup(IN4, GPIO.OUT)

    for pin in SENSOR_PINS:
        GPIO.setup(pin, GPIO.IN)

    pwm_left = GPIO.PWM(ENA, PWM_FREQ)
    pwm_right = GPIO.PWM(ENB, PWM_FREQ)
    pwm_left.start(0)
    pwm_right.start(0)


def set_motor_speeds(left_speed, right_speed):
    """left_speed / right_speed: -100..100 (sign = direction, magnitude = duty cycle %)"""
    left_speed = max(-MAX_SPEED, min(MAX_SPEED, left_speed))
    right_speed = max(-MAX_SPEED, min(MAX_SPEED, right_speed))

    if SIMULATED:
        print(f"[SIM] left={left_speed:.0f} right={right_speed:.0f}")
        return

    GPIO.output(IN1, GPIO.HIGH if left_speed >= 0 else GPIO.LOW)
    GPIO.output(IN2, GPIO.LOW if left_speed >= 0 else GPIO.HIGH)
    GPIO.output(IN3, GPIO.HIGH if right_speed >= 0 else GPIO.LOW)
    GPIO.output(IN4, GPIO.LOW if right_speed >= 0 else GPIO.HIGH)

    pwm_left.ChangeDutyCycle(abs(left_speed))
    pwm_right.ChangeDutyCycle(abs(right_speed))


def stop_motors():
    set_motor_speeds(0, 0)


# ----------------------------------------------------------------------
# MANUAL COMMANDS
# ----------------------------------------------------------------------
def cmd_forward():  set_motor_speeds(MANUAL_SPEED, MANUAL_SPEED)
def cmd_backward(): set_motor_speeds(-MANUAL_SPEED, -MANUAL_SPEED)
def cmd_left():     set_motor_speeds(-MANUAL_SPEED, MANUAL_SPEED)
def cmd_right():    set_motor_speeds(MANUAL_SPEED, -MANUAL_SPEED)
def cmd_stop():     stop_motors()

MANUAL_COMMANDS = {
    "F": cmd_forward,
    "B": cmd_backward,
    "L": cmd_left,
    "R": cmd_right,
    "S": cmd_stop,
}


# ----------------------------------------------------------------------
# AUTO MODE - line follower loop (runs in its own thread)
# ----------------------------------------------------------------------
def read_sensors():
    if SIMULATED:
        return [0, 0, 1, 1, 0, 0]  # fake "centered on line" reading
    return [GPIO.input(p) for p in SENSOR_PINS]


def auto_loop(stop_event):
    print("[AUTO] line-follower thread started")
    last_error = 0.0
    integral = 0.0

    while not stop_event.is_set():
        values = read_sensors()

        numerator = 0
        denominator = 0
        for v, w in zip(values, SENSOR_WEIGHTS):
            if v == 1:          # 1 = black line detected
                numerator += w
                denominator += 1

        if denominator > 0:
            error = numerator / denominator
        else:
            error = None  # line lost

        if error is None:
            stop_motors()
        else:
            integral = max(-100, min(100, integral + error))
            derivative = error - last_error
            pid_value = (KP * error) + (KI * integral) + (KD * derivative)
            last_error = error

            left_speed = BASE_SPEED + pid_value
            right_speed = BASE_SPEED - pid_value
            set_motor_speeds(left_speed, right_speed)

        time.sleep(0.02)  # ~50Hz control loop

    stop_motors()
    print("[AUTO] line-follower thread stopped")


def start_auto():
    global auto_thread
    auto_stop_event.clear()
    auto_thread = threading.Thread(target=auto_loop, args=(auto_stop_event,), daemon=True)
    auto_thread.start()


def stop_auto():
    auto_stop_event.set()
    if auto_thread is not None:
        auto_thread.join(timeout=2)
    stop_motors()


# ----------------------------------------------------------------------
# ROUTES
# ----------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')


@sock.route('/ws')
def ws_handler(ws):
    global mode
    print("[WS] client connected")

    # tell the client which mode we're currently in, so the UI can sync on connect
    with state_lock:
        ws.send(f"MODE:{mode}")

    while True:
        message = ws.receive()
        if not message:
            break

        msg = message.strip().upper()

        if msg in ("MODE:MANUAL", "MODE:AUTO"):
            new_mode = "MANUAL" if msg == "MODE:MANUAL" else "AUTO"
            with state_lock:
                if new_mode != mode:
                    if new_mode == "AUTO":
                        mode = "AUTO"
                        start_auto()
                    else:
                        stop_auto()
                        mode = "MANUAL"
                    print(f"[MODE] switched to {mode}")
            ws.send(f"ACK:MODE:{mode}")
            continue

        # manual drive commands
        with state_lock:
            current_mode = mode

        if current_mode != "MANUAL":
            ws.send("ERR:manual commands disabled while in AUTO mode")
            continue

        action = MANUAL_COMMANDS.get(msg)
        if action:
            action()
            ws.send(f"ACK:{msg}")
        else:
            ws.send(f"ERR:unknown command {msg}")

    print("[WS] client disconnected")


if __name__ == '__main__':
    gpio_setup()
    try:
        app.run(host='0.0.0.0', port=8000, debug=False)
    finally:
        stop_auto()
        stop_motors()
        if not SIMULATED:
            GPIO.cleanup()
