import os

# Every test runs against the scripted model, never the real API.
os.environ["LLM_PROVIDER"] = "fake"
os.environ["MONGODB_URI"] = ""
