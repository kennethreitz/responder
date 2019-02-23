import json

from . import status_codes


class WebSocketEndpoint:
    format = "json"

    async def __call__(self, ws):
        await self.on_connect(ws)

        close_code = status_codes.WS_1000_NORMAL_CLOSURE

        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.receive":
                    data = await self.decode(ws, message)
                    await self.on_receive(ws, data)
                elif message["type"] == "websocket.disconnect":
                    close_code = int(
                        message.get("code", status_codes.WS_1000_NORMAL_CLOSURE)
                    )
                    break
        except Exception as exc:
            close_code = status_codes.WS_1011_INTERNAL_ERROR
            raise exc from None
        finally:
            await self.on_disconnect(ws, close_code)

    async def decode(self, websocket, message):

        if self.format == "text":
            if "text" not in message:
                await websocket.close(code=status_codes.WS_1003_UNSUPPORTED_DATA)
                raise RuntimeError("Expected text websocket messages, but got bytes")
            return message["text"]

        elif self.format == "bytes":
            if "bytes" not in message:
                await websocket.close(code=status_codes.WS_1003_UNSUPPORTED_DATA)
                raise RuntimeError("Expected bytes websocket messages, but got text")
            return message["bytes"]

        elif self.format == "json":
            if message.get("text") is not None:
                text = message["text"]
            else:
                text = message["bytes"].decode("utf-8")

            try:
                return json.loads(text)
            except json.decoder.JSONDecodeError:
                await websocket.close(code=status_codes.WS_1003_UNSUPPORTED_DATA)
                raise RuntimeError("Malformed JSON data received.")

        assert self.format is None, f"Unsupported 'format' attribute {self.format}"
        return message["text"] if message.get("text") else message["bytes"]

    async def on_connect(self, ws):
        await ws.accept()

    async def on_receive(self, ws, data):
        """Override to handle an incoming websocket message"""

    async def on_disconnect(self, ws, close_code):
        """Override to handle a disconnecting websocket"""
