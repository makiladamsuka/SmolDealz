# ══════════════════════════════════════════════
#  AGENT 1 — IntentFinderAgent
#  Job: turn messy user text → clean intent dict
# ══════════════════════════════════════════════
from src.agents.model import model
from src.state import artifacts
from src.schemas import UserIntent
from smolagents import tool, ToolCallingAgent
from pydantic import ValidationError

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

4. COMPLETION:
   - Immediately call 'save_intent' once the item name is normalized, then call 'final_answer'.
"""

@tool
def save_intent(
    item: str, 
    quantity: int = 1, 
    budget: float | None = None, 
    region: str = "Sri Lanka"
) -> str:
    """Save the parsed user intent to the shared blackboard.

    Args:
        item:     The product name the user wants (e.g. 'mg90 servo').
        quantity: How many units they need (default: 1).
        budget:   Maximum total price in LKR if specified (optional).
        region:   Where to buy from (default: 'Sri Lanka').
    """
    try:
        # Pydantic validates types, ranges (e.g. quantity >= 1), and defaults
        intent = UserIntent(
            item=item,
            quantity=quantity,
            max_budget=budget,
            region=region
        )
        artifacts["intent"] = intent
        return f"SUCCESS: Intent validated and stored: {intent.model_dump()}"
    except ValidationError as e:
        return f"VALIDATION ERROR: Please fix parameters: {e}"

@tool
def ask_clarification(question: str) -> str:
    """Ask the user a clarifying question when the request is ambiguous.

    Args:
        question: The question to ask the user (e.g. 'Did you mean mg90 or sg90?').
    """
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
