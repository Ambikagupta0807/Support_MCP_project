"""
backend/app.py - FastAPI chat backend (port 8000).
Run from the project root:  python -m backend.app

Flow for every chat message:
    Browser --(JWT)--> /api/chat
        -> connect to MCP server with the SAME JWT as a Bearer token
        -> tools/list   (the MCP server only returns tools allowed for this role)
        -> LLM decides which tool(s) to call  -> tools/call on the MCP server
        -> loop until the LLM gives a final answer
    <-- reply + a trace of the tool calls (shown in the UI for teaching)

The browser NEVER talks to the MCP server directly.
"""

import json
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from common.auth import create_token, decode_token
from common.config import LLM_MODEL, MCP_SERVER_URL, PROJECT_ROOT
from common.prompts import build_system_prompt
from database.models import User

app = FastAPI(title="Telusko Support Chat")
llm = ChatOpenAI(model=LLM_MODEL)  # API key comes from OPENAI_API_KEY in .env

MAX_TOOL_ROUNDS = 8


# =============================================================================
# Login (authentication) - returns a JWT
# =============================================================================
class LoginRequest(BaseModel):
    email: str
    password: str


# When: POST /api/login, when the user submits the login form.
# Use:  Checks email + password and returns a JWT plus the user's details.
@app.post("/api/login")
def login(body: LoginRequest):
    user = User.get_by_email(body.email)
    if user is None or not user.check_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {"token": create_token(user.to_dict()), "user": user.to_dict()}


# When: Automatically by FastAPI (Depends) before every protected endpoint: /api/chat, /api/me, /api/users, /api/courses.
# Use:  Reads the 'Authorization: Bearer <jwt>' header, verifies it, and returns the user.
def get_current_user(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency: read 'Authorization: Bearer <jwt>' and verify it."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        claims = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"token": token, "id": int(claims["sub"]), "name": claims["name"],
            "email": claims["email"], "role": claims["role"], "scopes": claims["scope"].split()}


# =============================================================================
# MCP client - one connection per request, authenticated as the user
# =============================================================================
# When: By each endpoint that needs to talk to the MCP server.
# Use:  Opens an MCP connection authenticated with the user's JWT.
@asynccontextmanager 
async def mcp_client(token: str):
    """Connect to the MCP server, forwarding the user's JWT as a Bearer token."""
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
    async with http, Client(streamable_http_client(MCP_SERVER_URL, http_client=http)) as client:
        yield client


# When: In the chat loop, after each MCP tool call.
# Use:  Flattens an MCP tool result into plain text for the LLM.
def result_text(result) -> str:
    """Flatten an MCP CallToolResult into plain text for the LLM."""
    return "\n".join(c.text for c in result.content if getattr(c, "text", None)) or "(no output)"


# When: At the start of every POST /api/chat request.
# Use:  Converts the MCP tool list into tool definitions that ChatOpenAI.bind_tools accepts.
def mcp_tools_to_langchain(tools) -> list[dict]:
    """MCP tool definitions -> {name, description, parameters} dicts (LangChain converts them for the model)."""
    return [{"name": t.name, "description": t.description or "", "parameters": t.input_schema} for t in tools]


# =============================================================================
# Chat endpoint - the LLM tool-calling loop
# =============================================================================
class ChatMessage(BaseModel):
    role: str      # "user" or "assistant" (ai message) 
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


# When: POST /api/chat, every time the user sends a chat message.
# Use:  Runs the LLM tool-calling loop against the MCP server and returns the reply plus a tool-call trace.
@app.post("/api/chat")
async def chat(body: ChatRequest, user: dict = Depends(get_current_user)):
    # LangChain message objects: system prompt + the conversation so far
    messages = [SystemMessage(build_system_prompt(user))]
    for m in body.messages:
        if m.role == "user":
            messages.append(HumanMessage(m.content))
        elif m.role == "assistant":
            messages.append(AIMessage(m.content))
    
    trace = []  # tool calls made during this turn, shown in the UI

    async with mcp_client(user["token"]) as mcp:
        # Only the tools this user's scopes allow (the MCP server filters tools/list)
        llm_with_tools = llm.bind_tools(mcp_tools_to_langchain((await mcp.list_tools()).tools))

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response: AIMessage = await llm_with_tools.ainvoke(messages)
            except Exception as e:
                return {"reply": f"LLM error: {e}", "tool_calls": trace}

            # No tool calls -> this is the final answer
            if not response.tool_calls:
                return {"reply": response.text or "", "tool_calls": trace}

            # Keep the assistant's tool-call request in the history
            messages.append(response)

            # Execute each tool call on the MCP server (LangChain has already parsed the arguments into a dict)
            for tc in response.tool_calls:
                result = await mcp.call_tool(tc["name"], tc["args"])
                text = result_text(result)
                trace.append({"name": tc["name"], "arguments": tc["args"],
                              "is_error": bool(result.is_error), "result": text})
                messages.append(ToolMessage(content=text, tool_call_id=tc["id"]))

    return {"reply": "Sorry, I couldn't finish that request (too many tool calls).", "tool_calls": trace}


# =============================================================================
# Who am I - the sidebar's user, scopes and tool list
# =============================================================================
# When: GET /api/me, right after login, so the UI can show the user's scopes and tools.
# Use:  Returns the user, their scopes, and the tools the MCP server exposes to them.
@app.get("/api/me")
async def me(user: dict = Depends(get_current_user)):
    """The user, their scopes (from the JWT) and the tools the MCP server exposes to them."""
    async with mcp_client(user["token"]) as mcp:
        tools = (await mcp.list_tools()).tools
    return {"user": {k: user[k] for k in ("id", "name", "email", "role")}, "scopes": user["scopes"],
            "tools": [{"name": t.name, "description": t.description} for t in tools]}


# =============================================================================
# Manage users - the admin panel calls these; each one runs an MCP tool, so the
# MCP server still enforces the 'users:manage' scope.
# =============================================================================
class CreateUserRequest(BaseModel):
    name: str
    email: str
    password: str
    role: str
    scopes: list[str] | None = None      # None = role defaults


class UpdateUserRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None
    password: str | None = None          # None/empty = keep the current password
    scopes: list[str] | None = None
    use_role_defaults: bool = False


# When: By the /api/users endpoints below.
# Use:  Calls one MCP tool as the logged-in user and returns its JSON result (tool errors -> HTTP 400).
async def call_tool_json(token: str, name: str, arguments: dict) -> dict:
    async with mcp_client(token) as mcp:
        result = await mcp.call_tool(name, arguments)
    # Raise HTTP errors OUTSIDE the `async with` block (the MCP client runs in a task group)
    if result.is_error:
        detail = result_text(result)
        if detail.startswith("Error executing tool"):  # "Error executing tool x: <message>" -> "<message>"
            detail = detail.split(": ", 1)[-1]
        raise HTTPException(status_code=403 if detail.startswith("Access denied") else 400, detail=detail)
    return json.loads(result_text(result))


# When: GET /api/users, when an admin opens the Manage users panel.
# Use:  Returns all users plus the permission catalogue (all scopes, role defaults).
@app.get("/api/users")
async def list_users(user: dict = Depends(get_current_user)):
    return await call_tool_json(user["token"], "list_users", {})


# When: POST /api/users, when an admin saves the Add user form.
# Use:  Creates a user through the MCP tool create_user.
@app.post("/api/users")
async def create_user(body: CreateUserRequest, user: dict = Depends(get_current_user)):
    return await call_tool_json(user["token"], "create_user", body.model_dump(exclude_none=True))


# When: PUT /api/users/{user_id}, when an admin saves the Edit user form.
# Use:  Updates a user (and their permissions) through the MCP tool update_user.
@app.put("/api/users/{user_id}")
async def update_user(user_id: int, body: UpdateUserRequest, user: dict = Depends(get_current_user)):
    arguments = {"user_id": user_id, **body.model_dump(exclude_none=True)}
    if not arguments.get("password"):
        arguments.pop("password", None)
    return await call_tool_json(user["token"], "update_user", arguments)


# =============================================================================
# Buy a course - the sidebar's course list and Buy buttons
# =============================================================================
# When: GET /api/courses, after login, for users who can buy courses (sidebar course list).
# Use:  Lists courses (MCP tool list_courses) and marks the ones the user already has active access to.
@app.get("/api/courses")
async def list_courses(user: dict = Depends(get_current_user)):
    courses = (await call_tool_json(user["token"], "list_courses", {}))["courses"]
    enrollments = []
    if "me:read" in user["scopes"]:
        enrollments = (await call_tool_json(user["token"], "get_my_enrollments", {}))["enrollments"]
    active = {e["course_id"]: e["access_valid_till"] for e in enrollments if not e["access_expired"]}
    return {"courses": [{**c, "access_valid_till": active.get(c["id"])} for c in courses]}


class PurchaseRequest(BaseModel):
    course_title: str   # the title the learner saw; the MCP server refuses the purchase if it doesn't match the id


# When: POST /api/courses/{course_id}/purchase, when a learner confirms Buy in the sidebar.
# Use:  Buys the course through the MCP tool purchase_course (scope check happens on the MCP server).
@app.post("/api/courses/{course_id}/purchase")
async def purchase_course(course_id: int, body: PurchaseRequest, user: dict = Depends(get_current_user)):
    return await call_tool_json(user["token"], "purchase_course",
                                {"course_id": course_id, "course_title": body.course_title})


# When: GET /, when the browser opens the app.
# Use:  Serves the frontend page.
@app.get("/")
def index():
    return FileResponse(PROJECT_ROOT / "frontend" / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)