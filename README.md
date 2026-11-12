# ⚡ ScoutGrid: Autonomous Multi-Browser Price & Stock Agent

> **An AI-powered procurement engine built with Hugging Face `smolagents` and Playwright that parses natural-language hardware requests, discovers vendors, spins up parallel cloud browsers, verifies live quantity-based inventory via DOM inspection, and captures visual proof of the best deal.**

---

## 🎯 The Problem
Standard web scrapers break on modern JavaScript-heavy e-commerce sites, and standard LLM search tools (`DuckDuckGo` / `Tavily`) rely on stale search-engine cache snippets that often show outdated prices or "In Stock" labels for sold-out hardware. Even worse, if you need **3 sensors**, a store showing "In Stock" might only have **1 unit left**—something you only discover when trying to check out.

**ScoutGrid** solves this using a **Two-Stage Hybrid Architecture**:
1. **Constraint-Aware Query Parsing & Vendor Discovery:** Extracts exact user constraints (e.g., *"I need 3 VL53L0X ToF sensors and 2 TB6612FNG drivers under Rs. 5,000"*), identifies the component category, and autonomously discovers local or global stores—or accepts custom store URLs.
2. **Vision-Free Cloud Browsing + Live Cart Verification:** Spins up multiple isolated headless Chromium browsers concurrently in the cloud. It navigates pages using stripped-down **Interactive DOM / Accessibility Trees** (powered by fast text LLMs), tests actual stock quantities, and snaps high-resolution **annotated screenshots** with red bounding boxes around the price and stock count as human verification.

---

## 🔥 Smart Procurement Features (What Makes It Production-Grade)

### 1. Quantity-Aware Stock Validation & Split-Order Math
* **Strict Stock Filtering:** If you ask for `"3 ToF sensors"`, the agent doesn't just check if the item is "In Stock"—it parses the live stock badge (e.g., `8 in stock`) or simulates typing `3` into the quantity box inside the Playwright browser to verify the store won't throw an insufficient-stock error.
* **Split-Vendor Cart Optimization:** If Store A has the cheapest price (Rs. 800) but only has **2 units** left, and Store B has 10 units at Rs. 850, the agent calculates both options: buying all 3 from Store B vs. splitting the order ( accounting for extra shipping fees).

### 2. Multi-Item BOM (Bill of Materials) & Shipping Bundler
* When building a robot, buying 5 sensors from 5 different websites costs a fortune in separate delivery fees.
* You can pass a full list: *"3x VL53L0X ToF sensors, 1x ESP32-CAM, 2x MG996R servos"*. The agent evaluates **Total Landed Cost** (Item Subtotals + Delivery Fees) to see if buying everything from a single slightly pricier store is cheaper than splitting across multiple stores.

### 3. Variant & Pinout Guardrails (No Trap Listings)
* E-commerce stores frequently group cheap accessories on the same product page (e.g., listing a Rs. 50 plastic mounting bracket as the default price on an Ultrasonic Sensor page, or selling unsoldered vs. pre-soldered headers).
* The Playwright browser agent inspects dropdown selectors and product titles to ensure the price matches the **actual module**, not a cable or bracket.

### 4. Datasheet Equivalent & Drop-In Replacement Suggester
* If every local store is completely out of the `TB6612FNG` motor driver or only has 1 left when you need 4, the agent automatically checks electrical equivalents (like an `L298N` or `DRV8833`), warns you about voltage/size differences, and checks live stock for the alternative.

### 5. Bulk Tier Pricing Detection
* Many electronics stores offer price breaks (e.g., `1–4 units: Rs. 850 | 5+ units: Rs. 720`). If you ask for 4 units, the agent detects the bulk table in the DOM and alerts you if buying 5 units is nearly the same total cost.

### 6. Annotated Visual Proof & Price History Ledger
* Before closing each cloud browser, Playwright injects CSS to draw a bright red verification box around the **Unit Price**, **Available Quantity**, and **Variant Selected**, saving a timestamped `.png` screenshot alongside a local SQLite price log so you can track price inflation over time.

---

## 🧠 System Architecture & Workflow

```text
User Prompt: "I need to buy 3 VL53L0X ToF sensors in Sri Lanka" + [Optional: Store URLs]
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. Intent Parser & Discovery Agent (smolagents + Web Search)│
│  • Extracts: Item="VL53L0X ToF Sensor", Required_Qty=3      │
│  • Discovers top local vendors (TRONIC.LK, Nilambara, Scion)│
└────────────────────────────┬────────────────────────────────┘
                             │ Spawns Async Tasks
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ Cloud Tab #1 │      │ Cloud Tab #2 │      │ Cloud Tab #3 │
│ (Playwright) │      │ (Playwright) │      │ (Playwright) │
├──────────────┤      ├──────────────┤      ├──────────────┤
│ • Renders JS │      │ • Renders JS │      │ • Renders JS │
│ • Checks Qty │      │ • Checks Qty │      │ • Checks Qty │
│   (>= 3?)    │      │   (>= 3?)    │      │   (>= 3?)    │
│ • Boxes Price│      │ • Boxes Price│      │ • Boxes Price│
│   & Snaps PNG│      │   & Snaps PNG│      │   & Snaps PNG│
└──────┬───────┘      └──────┬───────┘      └──────┬───────┘
       └─────────────────────┼─────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Evaluator Agent & Cart Optimizer                         │
│  • Disqualifies stores with < 3 units (or calculates split) │
│  • Computes: (Unit_Price × 3) + Estimated_Shipping          │
│  • Outputs Markdown Comparison Table + Proof Screenshots    │
└─────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack
* **Agent Orchestration:** Hugging Face `smolagents` (`CodeAgent`, `DuckDuckGoSearchTool`, Custom `@tool` functions)
* **LLM Engine:** OpenRouter / Groq / Google AI Studio (`LiteLLMModel` using fast text models)
* **Browser Automation:** Playwright (Async Headless Chromium)
* **Backend & Database:** FastAPI + WebSockets + SQLite (for historical price tracking)
* **Frontend UI:** Streamlit / Next.js
* **Cloud Infrastructure:** Docker (`mcr.microsoft.com/playwright/python`) deployed on DigitalOcean / Azure Student VM

---

## 🚀 Getting Started

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/yourusername/scoutgrid-agent.git
cd scoutgrid-agent
pip install smolagents playwright fastapi uvicorn streamlit litellm duckduckgo-search
playwright install chromium
```

### 2. Configure Environment Variables
Create a `.env` file in the root directory:
```env
OPENROUTER_API_KEY=your_openrouter_key_here
# Optional: Use Groq or Gemini for faster text inference
GEMINI_API_KEY=your_gemini_key_here
```

### 3. Run the Application
```bash
streamlit run app.py
```

---

## 📸 Example Output

**User Prompt:** `"I need to buy 3 VL53L0X Time-of-Flight sensors for a rover build"`

| Rank | Store | Available Stock | Unit Price | Total (3 Units) | Verdict & Proof |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1 (Winner)** | **Nilambara Electronics** | **8 in stock** (Meets Qty: 3) | Rs. 850.00 | **Rs. 2,550.00** | ✅ Full quantity available ([Buy Link](#) + `proof_nilambara.png`) |
| **2** | **TRONIC.LK** | **14 in stock** (Meets Qty: 3) | Rs. 920.00 | **Rs. 2,760.00** | ✅ Full quantity available ([Buy Link](#) + `proof_tronic.png`) |
| **3 (Partial)** | **Maker.lk** | ⚠️ **Only 1 left** | Rs. 790.00 | *N/A* | ❌ Insufficient stock for 3 units (`proof_maker.png`) |
| **4** | **Scion Electronics** | **0 in stock** | Rs. 800.00 | *N/A* | ❌ Out of Stock (`proof_scion.png`) |

> **💡 Agent Tip:** *Maker.lk has the lowest unit price (Rs. 790), but only 1 unit is available. Buying all 3 units from Nilambara Electronics saves you extra delivery fees compared to splitting your order.*

---

## 🗺️ Roadmap
- [x] Phase 1: Natural-Language Quantity Parser + Web Search Discovery + Single Playwright Browser
- [ ] Phase 2: Async Multi-Tab Cloud Browser Pool with Quantity & Variant Verification
- [ ] Phase 3: Multi-Item BOM Cart Optimizer (Minimize Combined Price + Shipping)
- [ ] Phase 4: Dockerized Cloud Deployment with Live Viewport Streaming