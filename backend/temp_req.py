import requests

url = "http://localhost:8000/run"
data = {"goal": "Test goal", "role": "admin"}
try:
    response = requests.post(url, data=data)
    print("STATUS:", response.status_code)
    try:
        print("RESULT:", response.json())
    except Exception as e:
        print("TEXT:", response.text)
except Exception as e:
    print("Fetch error:", e)
