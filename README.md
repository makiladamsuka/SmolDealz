# ⚡ SmolDealz (ScoutGrid): Autonomous Multi-Browser Price & Stock Agent

> **An AI-powered hardware procurement engine built with Hugging Face `smolagents` and Playwright that parses natural-language component requests, discovers regional vendors, runs concurrent headless browser sessions, verifies live quantity-based inventory via DOM inspection, and captures visual proof of the best deal.**

---

## 🎯 The Problem
Standard web scrapers break on modern JavaScript-heavy e-commerce platforms, while generic LLM search tools (`DuckDuckGo` / `Tavily`) rely on stale search-engine index snippets that often show outdated prices or "In Stock" labels for sold-out hardware. 

Even worse, if you need **3 sensors**, a store showing "In Stock" might only have **1 unit left**—something you only discover upon reaching checkout.

**SmolDealz** solves this using a **Two-Stage Hybrid Architecture**:
1. **Constraint-Aware Intent Parsing & Vendor Discovery:** Extracts exact user constraints (e.g., *"I need 3 VL53L0X ToF sensors and 2 TB6612FNG drivers under Rs. 5,000 in Sri Lanka"*), identifies target components, and queries regional store registries alongside live web search.
2. **Hybrid 3-Tier Headless Verification + Live Cart Probing:** Spins up isolated Chromium browser sessions concurrently. It leverages a low-latency 3-tier extraction pipeline (JSON-LD → Standard E-Commerce Heuristics → LLM Accessibility Tree fallback), tests actual stock quantities, and snaps high-resolution **annotated screenshots** with red bounding boxes around the price and stock count as visual proof.

---

## 💡 Key Architectural Improvements & Design Principles

### 1. Cost & Latency-Optimized 3-Tier Extraction
Passing raw HTML or full DOMs to LLMs causes massive token consumption and high latency. SmolDealz uses a tiered approach:
* **Tier 1 (Fast & Deterministic):** Parses structured data embedded in the page (`application/ld+json`, Schema.org `Product`, and OpenGraph tags).
* **Tier 2 (E-Commerce Heuristics):** Identifies price and stock badges using known patterns from dominant e-commerce frameworks (WooCommerce, Shopify, OpenCart, PrestaShop).
* **Tier 3 (LLM Fallback):** For custom or heavily obfuscated single-page apps (SPAs), it strips down the DOM into an **Accessibility Tree Snapshot** and lets a fast LLM extract price, variant, and stock.

### 2. Stealth Headless Browsing & Anti-Bot Protection
Many modern electronics retailers protect their storefronts behind Cloudflare or basic bot detection. SmolDealz integrates `playwright-stealth`, user-agent rotation, and asynchronous request pacing to ensure high scraping reliability without being blocked.

### 3. Quantity-Aware Stock Validation & Split-Order Math
* **Strict Stock Filtering:** If you ask for `"3 ToF sensors"`, the agent doesn't stop at an "In Stock" badge. It parses live stock counts (`"8 in stock"`) or simulates typing `3` into the quantity input to trigger any store-side inventory limit warnings.
* **Split-Vendor Cart Optimization:** If Store A has the cheapest price (Rs. 800) but only has **2 units**, and Store B has 10 units at Rs. 850, the agent calculates both total options: purchasing all 3 from Store B vs. splitting the order across both stores (factoring in duplicate shipping fees).

### 4. Multi-Item BOM (Bill of Materials) & Landed Cost Solver
Buying 5 sensors from 5 different stores incurs multiple delivery fees. SmolDealz evaluates **Total Landed Cost**:
$$\text{Landed Cost} = \sum (\text{Unit Price} \times \text{Quantity}) + \sum \text{Shipping Fees}$$
It highlights whether buying all parts from a slightly pricier single distributor is cheaper overall than paying separate shipping fees across multiple vendors.

### 5. Curated Regional Registry + Open Search Hybrid
In addition to general web searches, SmolDealz supports a curated registry (`data/stores.json`) for regional hobbyist and electronics suppliers (e.g., TRONIC.LK, Nilambara, Scion, Maker.lk), providing instant URL patterns and high-confidence scraping selectors.

### 6. Visual Proof & Audit Ledger
Before closing browser contexts, Playwright injects CSS to highlight the **Unit Price**, **Available Quantity**, and **Variant Selected** with a red border, saving a timestamped `.png` screenshot alongside a local SQLite price log.

---

## 🧠 System Architecture & Workflow

```text
User Request: "I need to buy 3 VL53L0X ToF sensors in Sri Lanka under Rs. 3,500"
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. Intent Parser & Discovery (smolagents + DuckDuckGo)      │
│  • Pydantic Output: Item="VL53L0X", Qty=3, MaxPrice=3500    │
│  • Discovers Vendors: Store Registry + Live Web Search      │
└────────────────────────────┬────────────────────────────────┘
                             │ Async Worker Pool (playwright-stealth)
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Browser #1   │     │ Browser #2   │     │ Browser #3   │
│ (TRONIC.LK)  │     │ (Nilambara)  │     │ (Maker.lk)   │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ • Tier 1/2   │     │ • Tier 1/2   │     │ • Tier 1/2   │
│   Extraction │     │   Extraction │     │   Extraction │
│ • Qty >= 3?  │     │ • Qty >= 3?  │     │ • Qty >= 3?  │
│ • Snap Proof │     │ • Snap Proof │     │ • Snap Proof │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       └─────────────────────┼────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Cart Optimizer & Landed Cost Solver                      │
│  • Filters out insufficient stock (or suggests split order) │
│  • Landed Cost = Subtotal + Shipping Estimate               │
│  • Outputs Ranked Markdown Table + Annotated Proof PNGs     │
└─────────────────────────────────────────────────────────────┘
```

---

## 📂 Recommended Project Structure

```text
SmolDealz/
├── .env.example                # Template for API keys
├── README.md                   # Project overview & documentation
├── requirements.txt            # Python package dependencies
├── app.py                      # Streamlit interactive dashboard
├── config.py                   # Central settings, timeouts, and thresholds
├── data/
│   ├── stores.json             # Curated store registry and selector hints
│   ├── price_history.db        # SQLite database for historical price tracking
│   └── screenshots/            # Saved visual proof PNGs
├── src/
│   ├── __init__.py
│   ├── models.py               # Pydantic schemas (Query, Offer, BOM, Store)
│   ├── parser.py               # smolagents intent and constraint parser
│   ├── discovery.py            # DuckDuckGo search and store registry matcher
│   ├── browser/
│   │   ├── __init__.py
│   │   ├── scraper.py          # Playwright async browser pool with stealth
│   │   ├── extractor.py        # 3-tier extractor (JSON-LD -> Heuristics -> LLM)
│   │   └── annotator.py        # CSS bounding box injection & screenshot capture
│   └── optimizer/
│       ├── __init__.py
│       └── landed_cost.py      # Split-order & shipping bundle calculator
└── tests/
    ├── test_parser.py          # Intent parser unit tests
    └── test_extractor.py       # Scraper & extraction tests
```

---

## 🛠️ Tech Stack
* **Agent Framework:** Hugging Face `smolagents` (`CodeAgent`, `@tool` decorators)
* **LLM Engine:** OpenRouter / Google Gemini / Groq (`LiteLLMModel` or direct API client)
* **Browser Automation:** Playwright Python (`playwright`, `playwright-stealth`)
* **Data Validation:** Pydantic v2
* **Storage & UI:** SQLite + Streamlit

---

## 🚀 Getting Started

### 1. Prerequisites & Environment Setup
Ensure you have Python 3.10+ installed.

```bash
# Clone the repository
git clone https://github.com/yourusername/SmolDealz.git
cd SmolDealz

# Create and activate a virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

*(If creating from scratch, install core dependencies:)*
```bash
pip install smolagents playwright playwright-stealth pydantic streamlit beautifulsoup4 litellm duckduckgo-search python-dotenv
playwright install chromium
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Set your API keys inside `.env`:
```env
# Required for smolagents LLM inference:
GEMINI_API_KEY=your_gemini_api_key_here
# Or OpenRouter / Groq:
OPENROUTER_API_KEY=your_openrouter_api_key_here
GROQ_API_KEY=your_groq_api_key_here

# Playwright configuration
HEADLESS=true
BROWSER_TIMEOUT_MS=15000
```

### 4. Run the Streamlit Dashboard
```bash
streamlit run app.py
```

---

## 📸 Example Output

**User Prompt:** `"I need to buy 3 VL53L0X Time-of-Flight sensors for a rover build in Sri Lanka"`

| Rank | Store | Stock Status | Unit Price | Total (3 Units) | Verdict & Proof |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1 (Winner)** | **Nilambara Electronics** | **8 in stock** (Meets Qty: 3) | Rs. 850.00 | **Rs. 2,550.00** | ✅ Full quantity available ([View Listing](#) · `proof_nilambara.png`) |
| **2** | **TRONIC.LK** | **14 in stock** (Meets Qty: 3) | Rs. 920.00 | **Rs. 2,760.00** | ✅ Full quantity available ([View Listing](#) · `proof_tronic.png`) |
| **3 (Partial)** | **Maker.lk** | ⚠️ **Only 1 left** | Rs. 790.00 | *N/A* | ❌ Insufficient stock for 3 units (`proof_maker.png`) |
| **4** | **Scion Electronics** | **0 in stock** | Rs. 800.00 | *N/A* | ❌ Out of Stock (`proof_scion.png`) |

> **💡 Agent Tip:** *Maker.lk offers the lowest unit price (Rs. 790), but has only 1 unit remaining. Purchasing all 3 from Nilambara Electronics saves you an extra Rs. 350 in split shipping fees.*

---

## 🗺️ Realistic Implementation Roadmap

- [ ] **Phase 0: Project Setup & Schemas**
  - Project directory scaffolding and virtual environment setup.
  - Pydantic models for user queries, vendor listings, and stock status.
- [ ] **Phase 1: Robust Headless Scraper MVP**
  - Playwright browser worker with `playwright-stealth`.
  - Tier-1 JSON-LD and Tier-2 standard e-commerce DOM extractor.
  - Visual element bounding box injection and proof screenshot capture.
- [ ] **Phase 2: Intent Parsing & Vendor Discovery Agent**
  - `smolagents` natural-language query parser (extracting part name, quantity, budget, location).
  - Hybrid discovery: Curated `data/stores.json` + DuckDuckGo search tool.
  - Concurrent async browser worker execution (`asyncio.gather` with concurrency limits).
- [ ] **Phase 3: Deep Stock & Cart Validation**
  - Playwright quantity input simulation (`input[type="number"]`) to uncover hidden inventory limits.
  - Bulk discount tier detection (`1-4`, `5-9`, `10+` tables).
- [ ] **Phase 4: Multi-Item BOM & Landed Cost Optimizer**
  - Multi-item parser (e.g., *"3x VL53L0X, 1x ESP32, 2x SG90 servos"*).
  - Landed cost optimization solver (subtotal + grouped shipping).
  - SQLite price history tracking.
- [ ] **Phase 5: Interactive Dashboard & Deployment**
  - Streamlit user interface with real-time browser progress updates and proof modals.
  - Docker containerization (`mcr.microsoft.com/playwright/python`).
