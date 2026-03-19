import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3"


def query_llm(prompt: str) -> str:

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "num_predict": 1024,    # limit output length
            "temperature": 0.2      # stable responses
        }
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=300)
        response.raise_for_status()

        data = response.json()

        return data.get("response", "No response from model")

    except requests.exceptions.RequestException as e:
        print("LLM request failed:", e)
        return "LLM error occurred"