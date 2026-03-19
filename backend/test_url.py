import urllib.request
import urllib.parse
import sys

data = urllib.parse.urlencode({'url':'https://mocki.io/v1/86a750b5-2604-47b3-91e2-cc5aaf1c7f2d', 'role':'analyst', 'goal':'analyze'}).encode('ascii')
req = urllib.request.Request('http://localhost:8000/run', data=data)

try:
    with urllib.request.urlopen(req, timeout=120) as response:
        print("SUCCESS")
        print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print("HTTPError:", e.code)
    print("Body:", e.read().decode('utf-8'))
except Exception as e:
    print("Error:", e)
    sys.exit(1)
