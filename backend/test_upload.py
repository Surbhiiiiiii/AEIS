import requests
import json

url = "http://localhost:8000/run"

def test_url():
    print("Testing URL submission...")
    data = {
        'url': 'https://mocki.io/v1/86a750b5-2604-47b3-91e2-cc5aaf1c7f2d',
        'role': 'admin',
        'goal': 'Analyze endpoint'
    }
    response = requests.post(url, data=data)
    print(f"URL Test Status: {response.status_code}")
    if response.status_code == 200:
        res_json = response.json()
        print(f"Metrics: {res_json.get('metrics')}")
    else:
        print(response.text)

def test_dataset():
    print("\nTesting Dataset submission...")
    files = {'file': ('test_data.csv', 'id,status,message\n1,failed,timeout error\n2,success,ok\n3,failed,database locked', 'text/csv')}
    data = {
        'role': 'admin',
        'goal': 'Find the root cause of the failures in this CSV logs dataset'
    }
    response = requests.post(url, files=files, data=data)
    print(f"Dataset Test Status: {response.status_code}")
    if response.status_code == 200:
        res_json = response.json()
        print(f"Metrics: {res_json.get('metrics')}")
        print(f"Insights: {res_json.get('analysis_insights')}")
    else:
        print(response.text)

if __name__ == "__main__":
    try:
        test_url()
        test_dataset()
    except Exception as e:
        print(f"Error: {e}")
