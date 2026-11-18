"""
Minimal FastAPI + WebSocket learning server.

Run from project root:
    uv run uvicorn test.ws_learn.backend:app --port 8000

What this teaches:
1. FastAPI WebSocket endpoint (`/ws`) keeping a persistent connection open.
2. Bridging a blocking Python thread (`input()` / terminal) to async WebSockets
   using `asyncio.Queue` and `loop.call_soon_threadsafe(...)`.
   (This is the exact same pattern used to stream `smolagents` `.run()` later!)
3. Two-way communication: type in the terminal -> appears in React;
   type in React -> appears in the terminal.
"""

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Active browser connections
connected_clients: set[WebSocket] = set()

# Queue that bridges the blocking terminal thread -> async WebSocket sender
outgoing_queue: asyncio.Queue[dict] = asyncio.Queue()


def terminal_input_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Runs in a background thread so `input()` doesn't freeze FastAPI."""
    print("\n" + "=" * 50)
    print("💬 Type anything here and press Enter to send to React!")
    print("=" * 50 + "\n")

    while True:
        try:
            text = input()
        except EOFError:
            break

        text = text.strip()
        if not text:
            continue

        event = {
            "type": "terminal",
            "text": text,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        # Safely hand the message from this sync thread to the async event loop
        loop.call_soon_threadsafe(outgoing_queue.put_nowait, event)


async def broadcast_worker() -> None:
    """Pulls events from `outgoing_queue` and sends them to all React tabs."""
    while True:
        event = await outgoing_queue.get()
        payload = json.dumps(event)

        dead_clients = []
        for ws in list(connected_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead_clients.append(ws)

        for ws in dead_clients:
            connected_clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    # Start the async broadcaster task
    broadcaster = asyncio.create_task(broadcast_worker())

    # Start the blocking terminal reader in a daemon thread
    thread = threading.Thread(target=terminal_input_loop, args=(loop,), daemon=True)
    thread.start()

    yield

    broadcaster.cancel()


app = FastAPI(lifespan=lifespan)

# Allow React dev server (localhost:5173) to talk to this backend (localhost:8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    print(f"🟢 React connected! (total clients: {len(connected_clients)})")

    # Send a welcome event so React knows the pipe works immediately
    await websocket.send_text(
        json.dumps(
            {
                "type": "system",
                "text": "Connected to FastAPI WebSocket server!",
                "time": datetime.now().strftime("%H:%M:%S"),
            }
        )
    )

    try:
        while True:
            # Wait for any message sent from React (e.g. steering/chat input)
            raw = await websocket.receive_text()
            data = json.loads(raw)
            print(f"📩 [From React]: {data.get('text', raw)}")
    except WebSocketDisconnect:
        connected_clients.discard(websocket)
        print(f"🔴 React disconnected. (total clients: {len(connected_clients)})")
