import { useEffect, useRef, useState } from "react";
import "./App.css";

export default function App() {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const wsRef = useRef(null);

  useEffect(() => {
    // 1. Open a persistent WebSocket connection to FastAPI
    const ws = new WebSocket("ws://localhost:8000/ws");
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
    };

    // 2. Every time Python calls `websocket.send_text(...)`, this runs
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMessages((prev) => [...prev, data]);
    };

    ws.onclose = () => {
      setConnected(false);
    };

    // Clean up when component unmounts (important in React StrictMode)
    return () => {
      ws.close();
    };
  }, []);

  // 3. Send a message from React -> Python terminal
  const handleSend = (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return;
    }

    wsRef.current.send(JSON.stringify({ type: "chat", text }));
    setMessages((prev) => [
      ...prev,
      { type: "frontend", text, time: new Date().toLocaleTimeString() },
    ]);
    setInput("");
  };

  return (
    <div className="container">
      <header className="header">
        <div>
          <h1>WebSocket Playground</h1>
          <p className="subtitle">
            Type in your Python terminal → watch it appear here live
          </p>
        </div>
        <span className={`badge ${connected ? "online" : "offline"}`}>
          {connected ? "Connected" : "Disconnected"}
        </span>
      </header>

      <main className="feed">
        {messages.length === 0 ? (
          <p className="empty">
            Waiting for messages from the backend terminal...
          </p>
        ) : (
          messages.map((msg, idx) => (
            <div key={idx} className={`msg-card ${msg.type}`}>
              <div className="msg-meta">
                <span className="sender">
                  {msg.type === "terminal"
                    ? "Backend Terminal"
                    : msg.type === "frontend"
                    ? "You (React)"
                    : "System"}
                </span>
                <span className="time">{msg.time}</span>
              </div>
              <div className="msg-text">{msg.text}</div>
            </div>
          ))
        )}
      </main>

      <form className="chat-bar" onSubmit={handleSend}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Send a message back to the Python terminal..."
          disabled={!connected}
        />
        <button type="submit" disabled={!connected}>
          Send
        </button>
      </form>
    </div>
  );
}
