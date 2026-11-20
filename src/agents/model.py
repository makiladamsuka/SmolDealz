import os
from dotenv import load_dotenv

load_dotenv()

# Must be set BEFORE importing smolagents / litellm
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm
from smolagents import LiteLLMModel

litellm.suppress_debug_info = True
litellm.set_verbose = False

model = LiteLLMModel(
    model_id=os.getenv("OPENROUTER_MODEL", "openrouter/openrouter/free"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
)
