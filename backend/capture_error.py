import requests

BASE_URL = "http://localhost:8000"

try:
    # Step 1: Login to get a JWT token (use admin credentials)
    login_res = requests.post(f"{BASE_URL}/auth/login", json={
        "username": "admin",
        "password": "Admin@123"
    })
    token = login_res.json().get("token", "")

    # Step 2: Call /run with the token in the Authorization header
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        'url': 'https://mocki.io/v1/86a750b5-2604-47b3-91e2-cc5aaf1c7f2d',
        'goal': 'Analyze endpoint'
    }
    response = requests.post(f"{BASE_URL}/run", data=data, headers=headers)

    with open("error_trace.txt", "w") as f:
        f.write(str(response.status_code) + "\n" + response.text)

except Exception as e:
    with open("error_trace.txt", "w") as f:
        f.write(str(e))
