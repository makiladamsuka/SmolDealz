# ══════════════════════════════════════════════
#  AGENT 1 — IntentFinderAgent
#  Job: turn messy user text → clean intent dict
# ══════════════════════════════════════════════
import os
from typing import Callable
from src.agents.model import model
from src.state import artifacts
from src.schemas import UserIntent
from smolagents import tool, ToolCallingAgent
from pydantic import ValidationError

_clarification_handler: Callable[[str], str] | None = None
_interactive_clarification: bool = os.getenv("SMOLDEALZ_NON_INTERACTIVE", "").lower() not in {"1", "true", "yes"}


def set_clarification_mode(
    interactive: bool = True,
    handler: Callable[[str], str] | None = None,
) -> None:
    """Configure how ask_clarification obtains answers (CLI input, callback, or disabled)."""
    global _interactive_clarification, _clarification_handler
    _interactive_clarification = interactive
    _clarification_handler = handler

instructions="""
You are an expert e-commerce hardware & electronics shopping intent parser.
Your job is to convert raw, conversational user requests into clean, searchable component catalog terms.

Follow these universal rules:

1. PRODUCT NORMALIZATION (E-Commerce Ready):
   - Strip conversational filler words ("cheap", "best", "looking for", "for my project", "I need").
   - Separate numbers/quantities and locations — they belong in 'quantity' and 'region', NOT in the 'item' name.
   - Preserve all critical technical specifications:
     * Model / Part Numbers: (e.g., 'NE555', 'LM35', 'ESP32', 'MG90S', 'A4988', 'TB6612FNG')
     * Values & Ratings: (e.g., '10k ohm', '220uF 25V', '12V 2A', '5V relay')
     * Form factors / Packages: (e.g., 'DIP-8', 'SMD 0805', 'NEMA 17')

2. STORE SEARCH FRIENDLINESS:
   - Online store search engines use keyword matching. The 'item' string should match standard component store listings:
     * "cheap blue micro servo" -> "SG90 Micro Servo Motor"
     * "5v step down buck" -> "LM2596 Buck Converter 5V"
     * "10k pot" -> "10K Rotary Potentiometer"
     * "soldering rod" -> "Soldering Iron"

3. CLARIFICATION RULE:
   - Only call 'ask_clarification' if a critical parameter is completely missing that prevents finding the item (e.g., "I need a capacitor" without capacitance/voltage, or "I need a stepper motor" without size/torque).
   - If the user names a recognizable product or part number, do NOT ask extra questions — use standard defaults (quantity=1, budget=None, region='Sri Lanka').

4. FOLLOW-UP REFINEMENTS:
   - If a 'Previous intent:' is provided, treat the new message as an update to it: keep unchanged fields (e.g. region, quantity, budget, category, or item) unless the user overrides them.
   - Set 'category' to the broad product domain (e.g. 'electronics', 'fashion', 'groceries', 'home', 'general').

5. COMPLETION:
   - Only pass 'budget' if the user explicitly stated a price limit; otherwise omit it (never pass 0 or 1 as a placeholder).
   - Call 'save_intent' ONCE, then immediately call 'final_answer' EXACTLY ONCE on the next step (do NOT call 'save_intent' again, and NEVER call 'final_answer' multiple times in one step).
"""

@tool
def save_intent(
    item: str, 
    quantity: int = 1, 
    budget: float | None = None, 
    region: str = "Sri Lanka",
    category: str = "electronics",
) -> str:
    """Save the parsed user intent to the shared blackboard.

    Args:
        item:     The product name the user wants (e.g. 'mg90 servo').
        quantity: How many units they need (default: 1).
        budget:   Maximum total price in LKR ONLY if explicitly stated by the user; otherwise omit (None). Never guess 1.
        region:   Where to buy from (default: 'Sri Lanka').
        category: Product domain/category (e.g. 'electronics', 'fashion', 'groceries', 'general').
    """
    try:
        # Guard against small LLMs passing placeholder budget=0 or 1 when omitted
        if budget is not None and budget <= 1:
            budget = None
        # Pydantic validates types, ranges (e.g. quantity >= 1), and defaults
        intent = UserIntent(
            item=item,
            quantity=quantity,
            max_budget=budget,
            region=region,
            category=category.strip().lower() or "electronics",
        )
        artifacts["intent"] = intent
        return (
            f"SUCCESS: Intent validated and stored: {intent.model_dump()}. "
            "Do NOT call save_intent again — immediately call final_answer now."
        )
    except ValidationError as e:
        return f"VALIDATION ERROR: Please fix parameters: {e}"

@tool
def ask_clarification(question: str) -> str:
    """Ask the user a clarifying question when the request is ambiguous.

    Args:
        question: The question to ask the user (e.g. 'Did you mean mg90 or sg90?').
    """
    if _clarification_handler is not None:
        user_response = _clarification_handler(question).strip()
        return f"User answered: '{user_response}'"
    if not _interactive_clarification:
        return (
            "Interactive clarification is disabled. "
            "Proceed using reasonable defaults and call save_intent."
        )
    print(f"\n❓ [Agent Question]: {question}")
    user_response = input("👉 Your answer: ").strip()
    return f"User answered: '{user_response}'"


intent_agent = ToolCallingAgent(
    tools=[save_intent, ask_clarification],
    model=model,
    name="IntentFinderAgent",
    description="Parses user shopping requests into structured intent.",
    instructions=instructions,
    max_steps=4, 
)


if __name__ == "__main__":
    user_query = input("Enter your shopping query: ")
    
    intent_agent.run(
        f"Parse this request into a shopping intent: '{user_query}'. "
        "If the item is not specified, ask clarification. "
        "Otherwise, save the intent and provide the final answer."
    )
    
    print("\n" + "="*40)
    print(" BlackBoard artifacts['intent']:")
    print("="*40)
    print(artifacts.get("intent"))
