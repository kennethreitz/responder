import requests
from wsgiadapter import WSGIAdapter

from app import api

s = requests.Session()
s.mount("http://staging/", WSGIAdapter(api))
r = s.get("http://staging/")
print(r)
