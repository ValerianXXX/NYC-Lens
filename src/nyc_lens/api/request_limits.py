"""Bound a job's HTTP body before the JSON parser or Pydantic allocates it."""
from starlette.responses import JSONResponse


class AnalysisBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/analysis-jobs":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > 262_144:
                response = JSONResponse(status_code=413, content={"detail": {"code": "ANALYSIS_INPUT_SIZE_LIMIT", "started": False}})
                return await response(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body"):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        await self.app(scope, bounded_receive, send)
