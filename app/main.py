"""FastAPI server: chat API + static chat UI + MCP server at /mcp."""
import contextlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
from app.agent import Agent  # noqa: E402  (after env is loaded)
from app.mcp_server import build_mcp, http_app  # noqa: E402

log = logging.getLogger("uvicorn.error")
STATIC = Path(__file__).parent / "static"

agent = Agent()
# MCP server shares the already-loaded data and vector index with the chatbot.
mcp = build_mcp(agent.store, agent.retriever)
mcp_http = http_app(mcp)


@contextlib.asynccontextmanager
async def lifespan(_app):
    async with mcp.session_manager.run():  # sub-app lifespans don't run when mounted
        yield


app = FastAPI(title="Real Estate Chatbot", lifespan=lifespan)


class Msg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    messages: list[Msg] = Field(min_length=1, max_length=30)


@app.post("/api/chat")
def chat(req: ChatRequest):
    if req.messages[-1].role != "user":
        raise HTTPException(400, "last message must be from the user")
    try:
        return agent.chat([m.model_dump() for m in req.messages])
    except Exception as e:
        log.exception("chat failed")
        msg = str(e)
        if "429" in msg:
            msg = "The free AI model is rate-limited right now. Please try again in a minute."
        raise HTTPException(502, msg)


@app.get("/api/stats")
def stats():
    return agent.store.overview()


@app.get("/health")
def health():
    return {"ok": True, "has_key": bool(os.getenv("OPENROUTER_API_KEY"))}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# Last, so the routes above take precedence: serves POST/GET /mcp (Streamable HTTP).
app.mount("/", mcp_http)
