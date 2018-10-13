#!/usr/bin/env python

from quart import Quart, websocket

app = Quart(__name__)

@app.route('/')
async def hello():
    return 'hello'

if __name__ == "__main__":
    app.run()
