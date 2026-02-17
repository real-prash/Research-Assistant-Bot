import os
from dotenv import load_dotenv, find_dotenv
from langchain_groq import ChatGroq
from langchain_community.tools.tavily_search import TavilySearchResults

# 1. Load Environment Variables
found_dotenv = find_dotenv()
if found_dotenv:
    load_dotenv(found_dotenv)
else:
    # Optional: Log warning if using a logger
    pass

# 2. Validation
if not os.getenv("GROQ_API_KEY"):
    raise ValueError("GROQ_API_KEY is missing. Please set it in your .env file.")
if not os.getenv("TAVILY_API_KEY"):
    raise ValueError("TAVILY_API_KEY is missing. Please set it in your .env file.")

# 3. Initialize Models
# The Planner: Smart, Structured (Llama 3.3 70B)
llm_planner = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0
)

# The Worker: Fast, High Rate Limit (Llama 4 17B)
# Includes Auto-Retry for Rate Limits
llm_worker = ChatGroq(
    model="meta-llama/llama-4-scout-17b-16e-instruct",
    temperature=0
).with_retry(
    stop_after_attempt=8,
    wait_exponential_jitter=True 
)

# 4. Initialize Tools
# Max results set to 3 for better data coverage
tavily_search = TavilySearchResults(max_results=3)