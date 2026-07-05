# pip install websockets
import asyncio
import websockets

# --- replace with your actual motor control ---
def forward():  print("Moving forward")
def backward(): print("Moving backward")
def turn_left(): print("Turning left")
def turn_right(): print("Turning right")
def stop():     print("Stopping")
# ------------------------------------------------

COMMANDS = {"F": forward, "B": backward, "L": turn_left, "R": turn_right, "S": stop}

async def handler(websocket):
    print("Client connected")
    async for message in websocket:
        cmd = message.strip().upper()
        action = COMMANDS.get(cmd)
        if action:
            action()
            await websocket.send(f"ACK:{cmd}")
        else:
            await websocket.send(f"ERR:unknown command {cmd}")

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("WebSocket server running on port 8765")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
