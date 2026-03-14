import requests

# Use Apple's Advanced stream which has an embedded player suitable for Playwright if needed, 
# although we want to test our API endpoints' return URLs natively.
# We will use our own `/extract` endpoint but maybe bypass playwright if it downloads directly.

res = requests.post("http://localhost:8000/api/prepare-remux", json={"session_id": "dummy_test"})
data = res.json()
print("Prepare Remux Response:", data)

res2 = requests.get("http://localhost:8000/proxy/segment?url=http://devimages.apple.com/iphone/samples/bipbop/bipbopall.m3u8")
print("Segment Proxy Code:", res2.status_code)
if res2.status_code == 200:
    for line in res2.text.splitlines()[:5]:
        print(line)

