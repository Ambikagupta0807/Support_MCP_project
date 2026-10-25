"""
mcp_server/server.py - the Support MCP server (port 8001, Streamable HTTP at /mcp).

What this file demonstrates:
  1. AUTHENTICATION  - every request must carry a valid JWT (Bearer token).
                       JWTTokenVerifier checks signature, expiry, issuer, audience.
  2. AUTHORIZATION   - the JWT carries SCOPES like "tickets:read" or "certificates:issue".
                       TOOL_SCOPES says which scope each tool needs; check_access() enforces it.
                       Data-level rules too: without "tickets:read_all" you only see YOUR tickets.
  3. TOOL VISIBILITY - tools/list only shows the tools your scopes allow.

Note: this uses mcp 2.x, where FastMCP was renamed to MCPServer.
Run from the project root:  python -m mcp_server.server
"""

import sqlite3
import uuid

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from common.auth import decode_token
from common.config import BACKEND_PORT, MCP_PORT
from common.permissions import ALL_SCOPES, ROLE_SCOPES
from common.prompts import MCP_SERVER_INSTRUCTIONS
from database.models import (
    FAQ,
    ROLES,
    TICKET_CATEGORIES,
    TICKET_PRIORITIES,
    TICKET_STATUSES,
    Course,
    Enrollment,
    Payment,
    Ticket,
    User,
    hash_password,
)

SUPPORT_MAX_EXTENSION_DAYS = 30
MIN_PASSWORD_LENGTH = 8


# =============================================================================
# 1. AUTHORIZATION TABLE - which scope does each tool need?
#    (Which scopes each ROLE gets is defined in common/permissions.py)
#    The "# Who:" line above each tool lists the roles that get its scope by default.
#    An admin can also grant a scope to any single user from Manage users (custom permissions).
# =============================================================================
TOOL_SCOPES = {
    # everyone
    "search_faq":               "faq:read",
    "list_courses":             "courses:read",
    "get_ticket":               "tickets:read",
    # learner
    "get_my_enrollments":       "me:read",
    "get_my_payments":          "me:read",
    "check_certificate_status": "me:read",
    "purchase_course":          "courses:purchase",
    "get_my_tickets":           "me:read",
    "raise_ticket":             "tickets:create",
    "reply_to_my_ticket":       "tickets:reply",
    # support + admin
    "list_open_tickets":        "tickets:read_all",
    "search_learners":          "learners:read",
    "get_learner_details":      "learners:read",
    "assign_ticket_to_me":      "tickets:manage",
    "assign_ticket":            "tickets:manage",
    "reply_to_ticket":          "tickets:manage",
    "update_ticket_status":     "tickets:manage",
    "extend_course_access":     "access:extend",
    "remove_learner_from_course": "enrollments:remove",
    # admin
    "issue_certificate":        "certificates:issue",
    "add_faq":                  "faq:write",
    "get_support_stats":        "stats:read",
    "change_user_role":         "users:manage",
    "list_users":               "users:manage",
    "create_user":              "users:manage",
    "update_user":              "users:manage",
}


# =============================================================================
# 2. AUTHENTICATION - verify the JWT sent by the backend
# =============================================================================
class JWTTokenVerifier:
    """Implements the SDK's TokenVerifier protocol: token in -> AccessToken (or None = 401)."""

    # When: By the MCP SDK on every incoming HTTP request, before anything else runs.
    # Use:  Checks the Bearer JWT; returns the caller's scopes and claims, or None (-> 401).
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = decode_token(token)  # signature + exp + iss + aud
        except Exception as e:
            print(f"[auth] rejected token: {e}")
            return None

        return AccessToken(
            token=token,
            client_id="telusko-backend",
            scopes=claims["scope"].split(),  # "faq:read tickets:read" -> ["faq:read", "tickets:read"]
            expires_at=claims["exp"],
            subject=claims["sub"],           # user id
            claims=claims,                   # name, email, role, ...
        )


# When: Inside tools, helpers and check_access whenever the caller's identity is needed.
# Use:  Returns the current caller's id, name, email, role and scopes from the verified token.
def current_user() -> dict:
    """Who is calling, and what may they do? Read from the verified token for this request."""
    token = get_access_token()
    if token is None or token.claims is None:
        raise ToolError("Not authenticated")
    return {
        "id": int(token.subject),
        "name": token.claims["name"],
        "email": token.claims["email"],
        "role": token.claims["role"],
        "scopes": token.scopes,
    }


# When: Inside tools for data-level rules (e.g. 'tickets:read_all', 'access:extend_unlimited').
# Use:  True if the current caller has the given scope.
def has_scope(scope: str) -> bool:
    return scope in current_user()["scopes"]


class AccessDenied(ToolError):
    """Raised inside a tool when the caller may use the tool but not on THIS data."""


# =============================================================================
# 3. THE GATEKEEPER - runs before every MCP request (like a filter in Spring Security)
# =============================================================================
# When: As middleware on EVERY MCP request (tools/list, tools/call, ...).
# Use:  Hides tools the caller can't use, and blocks calls without the required scope.
async def check_access(ctx, call_next):
    """
    tools/list -> return only the tools the caller has a scope for.
    tools/call -> refuse the call if the caller lacks the tool's scope.
    """
    token = get_access_token()
    scopes = token.scopes if token else []

    # ---- Listing tools: hide what you can't use ----
    if ctx.method == "tools/list":
        result = await call_next(ctx)
        result["tools"] = [t for t in result["tools"] if TOOL_SCOPES.get(t["name"]) in scopes]
        return result

    # ---- Calling a tool: check the scope first ----
    if ctx.method == "tools/call":
        tool_name = ctx.params["name"]
        required = TOOL_SCOPES.get(tool_name)

        if required not in scopes:
            message = f"Access denied: '{tool_name}' needs the '{required}' permission."
            denied = CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)
            return denied.model_dump(by_alias=True, exclude_none=True, mode="json")  # middleware returns JSON

        return await call_next(ctx)

    # Anything else (initialize, ping, ...) passes straight through
    return await call_next(ctx)


mcp = MCPServer(
    name="Telusko Support",
    instructions=MCP_SERVER_INSTRUCTIONS,
    token_verifier=JWTTokenVerifier(),
    auth=AuthSettings(
        issuer_url=f"http://127.0.0.1:{BACKEND_PORT}",               # who issues tokens
        resource_server_url=f"http://127.0.0.1:{MCP_PORT}/mcp",      # this server
        validate_token_resource=False,  # our verifier already checks the "aud" claim
    ),
    middleware=[check_access],
)


# --- small helpers -----------------------------------------------------------
# When: By the ticket tools (get_ticket, reply_to_my_ticket, assign_ticket_to_me, ...).
# Use:  Loads a ticket; without 'tickets:read_all' the caller may only open their own tickets.
def load_ticket(ticket_id: int) -> Ticket:
    """Load a ticket. Without 'tickets:read_all' you can only open your own tickets."""
    user = current_user()
    ticket = Ticket.get(ticket_id)
    if ticket is None:
        raise ToolError(f"Ticket #{ticket_id} not found.")
    if not has_scope("tickets:read_all") and ticket.user_id != user["id"]:
        raise AccessDenied(f"Access denied: ticket #{ticket_id} does not belong to you.")
    return ticket


# When: By the get_ticket tool.
# Use:  Returns a ticket plus its messages (internal notes only for support/admin).
def ticket_with_messages(ticket: Ticket) -> dict:
    """Ticket + conversation. Internal notes only for callers with 'tickets:read_all'."""
    messages = ticket.messages(include_internal=has_scope("tickets:read_all"))
    return {**ticket.to_dict(), "messages": [m.to_dict() for m in messages]}


# =============================================================================
# 4. TOOLS - everyone ("faq:read", "courses:read", "tickets:read")
# =============================================================================
# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'faq:read' scope.
# Use:  Searches the FAQ knowledge base.
# Who:  learner, support, admin
@mcp.tool()
def search_faq(query: str) -> dict:
    """Search the FAQ knowledge base (access, payments, certificates, technical issues)."""
    return {"results": [faq.to_dict() for faq in FAQ.search(query)]}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'courses:read' scope.
# Use:  Lists all courses.
# Who:  learner, support, admin
@mcp.tool()
def list_courses() -> dict:
    """List all Telusko courses with price (INR) and access period."""
    return {"courses": [c.to_dict() for c in Course.all()]}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'tickets:read' scope.
# Use:  Returns one ticket with its conversation.
# Who:  learner, support, admin
@mcp.tool()
def get_ticket(ticket_id: int) -> dict:
    """Get a ticket with its conversation. Learners can only open their own tickets."""
    return ticket_with_messages(load_ticket(ticket_id))


# =============================================================================
# 5. TOOLS - learner ("me:read", "tickets:create", "tickets:reply")
#    Always scoped to the caller's own user id from the JWT
# =============================================================================
# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'me:read' scope.
# Use:  Returns the caller's own enrollments.
# Who:  learner
@mcp.tool()
def get_my_enrollments() -> dict:
    """Get my enrolled courses with progress, access expiry and certificate status."""
    return {"enrollments": [e.to_dict() for e in Enrollment.for_user(current_user()["id"])]}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'me:read' scope.
# Use:  Returns the caller's own payment history.
# Who:  learner
@mcp.tool()
def get_my_payments() -> dict:
    """Get my payment history (success / failed / pending)."""
    return {"payments": [p.to_dict() for p in Payment.for_user(current_user()["id"])]}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'me:read' scope.
# Use:  Returns certificate eligibility/status for each of the caller's courses.
# Who:  learner
@mcp.tool()
def check_certificate_status() -> dict:
    """Check certificate eligibility (100% progress) and issue status for each of my courses."""
    return {"certificates": [
        {"course": e.course_title, "progress_percent": e.progress_percent, "status": e.certificate_status()}
        for e in Enrollment.for_user(current_user()["id"])
    ]}


# When: The LLM picks this tool during /api/chat, or the learner clicks Buy in the sidebar
#       (POST /api/courses/{course_id}/purchase); check_access confirms the 'courses:purchase' scope.
# Use:  Buys a course for the caller with a simulated payment: enrolls them, or renews expired access.
# Who:  learner
@mcp.tool()
def purchase_course(course_id: int, course_title: str) -> dict:
    """Buy a course for myself (simulated payment, always succeeds). Enrolls me, or renews my access if it expired.
    Pass BOTH the course id and its title exactly as returned by list_courses; the purchase is refused if they
    don't match. Confirm the course name and price with the learner before calling this."""
    user = current_user()
    course = Course.get(course_id)
    if course is None:
        raise ToolError(f"Course #{course_id} not found")
    # Safety net against a wrong id: the title must match the course being bought
    wanted, actual = course_title.strip().lower(), course.title.lower()
    if not wanted or (wanted not in actual and actual not in wanted):
        raise ToolError(f"Course #{course_id} is '{course.title}', not '{course_title}'. "
                        "Nothing was purchased. Check the id with list_courses and try again.")

    enrollment = Enrollment.find(user["id"], course.id)
    if enrollment and not enrollment.is_expired:
        raise ToolError(f"You already have access to '{course.title}' until {enrollment.access_valid_till}")

    # Simulated payment gateway: a real app would redirect to Razorpay/Stripe here and wait for confirmation
    reference = f"pay_SIM_{uuid.uuid4().hex[:10].upper()}"
    payment = Payment.create(user["id"], course.id, course.price_inr, "success", reference)

    if enrollment:  # expired -> renew
        enrollment.extend(course.access_days)
        action = "Access renewed"
    else:
        enrollment = Enrollment.create(user["id"], course.id, course.access_days)
        action = "Enrolled"
    return {"message": f"{action}: {course.title}", "course": course.title, "amount_inr": course.price_inr,
            "payment_id": payment.id, "payment_reference": reference,
            "enrollment_id": enrollment.id, "access_valid_till": enrollment.access_valid_till}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'tickets:create' scope.
# Use:  Validates the input and creates a new ticket for the caller.
# Who:  learner
@mcp.tool()
def raise_ticket(subject: str, category: str, message: str, priority: str = "medium") -> dict:
    """Raise a new support ticket. category: access|payment|certificate|technical|other. priority: low|medium|high."""
    if category not in TICKET_CATEGORIES:
        raise ToolError(f"category must be one of: {', '.join(TICKET_CATEGORIES)}")
    if priority not in TICKET_PRIORITIES:
        raise ToolError(f"priority must be one of: {', '.join(TICKET_PRIORITIES)}")
    ticket = Ticket.create(current_user()["id"], subject, category, priority, message)
    return {"ticket_id": ticket.id, "status": ticket.status, "message": "Ticket raised successfully."}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'me:read' scope.
# Use:  Lists the tickets the caller has raised.
# Who:  learner
@mcp.tool()
def get_my_tickets() -> dict:
    """List all tickets I have raised."""
    return {"tickets": [t.to_dict() for t in Ticket.for_user(current_user()["id"])]}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'tickets:reply' scope.
# Use:  Adds a reply to one of the caller's own tickets.
# Who:  learner
@mcp.tool()
def reply_to_my_ticket(ticket_id: int, message: str) -> dict:
    """Add a reply to one of my own tickets."""
    ticket = load_ticket(ticket_id)  # raises AccessDenied if it's not mine
    ticket.add_message(current_user()["id"], message)
    return {"ticket_id": ticket_id, "message": "Reply added."}


# =============================================================================
# 6. TOOLS - support + admin ("tickets:read_all", "tickets:manage", "learners:read", "access:extend",
#    "enrollments:remove")
# =============================================================================
# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'tickets:read_all' scope.
# Use:  Lists open and in-progress tickets (the support queue).
# Who:  support, admin
@mcp.tool()
def list_open_tickets(category: str | None = None) -> dict:
    """List open and in-progress tickets, optionally filtered by category."""
    return {"tickets": [t.to_dict() for t in Ticket.open_tickets(category)]}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'learners:read' scope.
# Use:  Finds learners by part of their name or email.
# Who:  support, admin
@mcp.tool()
def search_learners(query: str) -> dict:
    """Find learners by (part of) their name or email, e.g. "priya". Use this to get a learner's email."""
    return {"learners": [u.to_dict() for u in User.search_learners(query)]}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'learners:read' scope.
# Use:  Returns a learner's profile, enrollments, payments and tickets.
# Who:  support, admin
@mcp.tool()
def get_learner_details(email: str) -> dict:
    """Get a learner's profile, enrollments, payments and tickets by email."""
    learner = User.get_by_email(email)
    if learner is None or learner.role != "learner":
        raise ToolError(f"No learner found with email {email}")
    return {
        "learner": learner.to_dict(),
        "enrollments": [e.to_dict() for e in Enrollment.for_user(learner.id)],
        "payments": [p.to_dict() for p in Payment.for_user(learner.id)],
        "tickets": [t.to_dict() for t in Ticket.for_user(learner.id)],
    }


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'tickets:manage' scope.
# Use:  Assigns a ticket to the calling support/admin user.
# Who:  support, admin
@mcp.tool()
def assign_ticket_to_me(ticket_id: int) -> dict:
    """Assign a ticket to myself and mark it in_progress."""
    ticket = load_ticket(ticket_id)
    ticket.assign_to(User.get(current_user()["id"]))
    return {"ticket_id": ticket.id, "assigned_to": ticket.assigned_to_name, "status": ticket.status}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'tickets:manage' scope.
# Use:  Assigns a ticket to another support/admin user, found by name or email.
# Who:  support, admin
@mcp.tool()
def assign_ticket(ticket_id: int, assignee: str) -> dict:
    """Assign a ticket to another support/admin user (by name or email, e.g. "Neha") and mark it in_progress."""
    ticket = load_ticket(ticket_id)
    matches = User.search_support(assignee)
    if not matches:
        raise ToolError(f"No support/admin user matches '{assignee}'.")
    if len(matches) > 1:
        raise ToolError(f"'{assignee}' matches several users: {', '.join(u.email for u in matches)}. Use an email.")
    ticket.assign_to(matches[0])
    return {"ticket_id": ticket.id, "assigned_to": ticket.assigned_to_name, "status": ticket.status}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'tickets:manage' scope.
# Use:  Adds a support/admin reply or an internal note to any ticket.
# Who:  support, admin
@mcp.tool()
def reply_to_ticket(ticket_id: int, message: str, internal: bool = False) -> dict:
    """Reply to a ticket. Set internal=True for an internal note only support/admin can see (the learner will NOT see it)."""
    ticket = load_ticket(ticket_id)
    ticket.add_message(current_user()["id"], message, internal)
    return {"ticket_id": ticket_id, "internal": internal, "message": "Reply added."}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'tickets:manage' scope.
# Use:  Changes a ticket's status.
# Who:  support, admin
@mcp.tool()
def update_ticket_status(ticket_id: int, status: str) -> dict:
    """Change ticket status: open | in_progress | resolved | closed."""
    if status not in TICKET_STATUSES:
        raise ToolError(f"status must be one of: {', '.join(TICKET_STATUSES)}")
    ticket = load_ticket(ticket_id)
    ticket.set_status(status)
    return {"ticket_id": ticket_id, "status": status}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'access:extend' scope.
# Use:  Extends course access; support is capped at 30 days, admins are not.
# Who:  support (max 30 days), admin
@mcp.tool()
def extend_course_access(enrollment_id: int, days: int) -> dict:
    """Extend a learner's course access by N days. Role-based limits are enforced by the server."""
    if days <= 0:
        raise ToolError("days must be a positive number")
    if days > SUPPORT_MAX_EXTENSION_DAYS and not has_scope("access:extend_unlimited"):
        raise AccessDenied(
            f"Access denied: support agents can extend access by at most {SUPPORT_MAX_EXTENSION_DAYS} days. "
            "Ask an admin for longer extensions."
        )
    enrollment = Enrollment.get(enrollment_id)
    if enrollment is None:
        raise ToolError(f"Enrollment #{enrollment_id} not found")
    old_till = enrollment.access_valid_till
    enrollment.extend(days)
    return {"enrollment_id": enrollment_id, "course": enrollment.course_title,
            "old_valid_till": old_till, "new_valid_till": enrollment.access_valid_till}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'enrollments:remove' scope.
# Use:  Removes a learner from a course by deleting their enrollment (payment history is kept, no refund).
# Who:  support, admin
@mcp.tool()
def remove_learner_from_course(learner_email: str, course_id: int, course_title: str) -> dict:
    """Remove a learner from a course: deletes their enrollment, progress and certificate status for it.
    Payment history is kept and nothing is refunded. Pass BOTH the course id and its title exactly as returned by
    list_courses or get_learner_details; the removal is refused if they don't match. Confirm with the user first."""
    learner = User.get_by_email(learner_email)
    if learner is None or learner.role != "learner":
        raise ToolError(f"No learner found with email {learner_email}")
    course = Course.get(course_id)
    if course is None:
        raise ToolError(f"Course #{course_id} not found")
    # Safety net against a wrong id: the title must match the course being removed
    wanted, actual = course_title.strip().lower(), course.title.lower()
    if not wanted or (wanted not in actual and actual not in wanted):
        raise ToolError(f"Course #{course_id} is '{course.title}', not '{course_title}'. "
                        "Nothing was removed. Check the id and try again.")
    enrollment = Enrollment.find(learner.id, course.id)
    if enrollment is None:
        raise ToolError(f"{learner.name} is not enrolled in '{course.title}'")

    enrollment.delete()
    return {"message": f"Removed {learner.name} from '{course.title}'.", "learner": learner.email,
            "course": course.title, "removed_progress_percent": enrollment.progress_percent,
            "removed_access_valid_till": enrollment.access_valid_till,
            "certificate_was_issued": bool(enrollment.certificate_issued)}


# =============================================================================
# 7. TOOLS - admin ("certificates:issue", "faq:write", "stats:read", "users:manage", ...)
# =============================================================================
# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'certificates:issue' scope.
# Use:  Issues a certificate for a 100%-complete enrollment.
# Who:  admin
@mcp.tool()
def issue_certificate(enrollment_id: int) -> dict:
    """Issue a course certificate. Only allowed when progress is 100%."""
    enrollment = Enrollment.get(enrollment_id)
    if enrollment is None:
        raise ToolError(f"Enrollment #{enrollment_id} not found")
    if enrollment.certificate_issued:
        raise ToolError("Certificate already issued for this enrollment")
    if enrollment.progress_percent < 100:
        raise ToolError(f"Cannot issue certificate: progress is only {enrollment.progress_percent}%")
    enrollment.issue_certificate()
    return {"enrollment_id": enrollment_id, "course": enrollment.course_title, "certificate_issued": True}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'faq:write' scope.
# Use:  Adds a new FAQ.
# Who:  admin
@mcp.tool()
def add_faq(question: str, answer: str, category: str) -> dict:
    """Add a new FAQ to the knowledge base."""
    faq = FAQ.create(question, answer, category)
    return {"faq_id": faq.id, "message": "FAQ added."}


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'stats:read' scope.
# Use:  Returns dashboard numbers for tickets and payments.
# Who:  admin
@mcp.tool()
def get_support_stats() -> dict:
    """Dashboard numbers: tickets by status/category, tickets per agent, payments by status."""
    return {
        "tickets_by_status": Ticket.count_by("status"),
        "tickets_by_category": Ticket.count_by("category"),
        "tickets_per_agent": Ticket.count_per_agent(),
        "payments_by_status": Payment.summary_by_status(),
    }


# When: The LLM picks this tool during /api/chat (MCP tools/call), after check_access confirms the 'users:manage' scope.
# Use:  Changes another user's role.
# Who:  admin
@mcp.tool()
def change_user_role(email: str, new_role: str) -> dict:
    """Change a user's role to learner | support | admin."""
    if new_role not in ROLES:
        raise ToolError("new_role must be one of: learner, support, admin")
    if email.strip().lower() == current_user()["email"]:
        raise ToolError("You cannot change your own role")
    target = User.get_by_email(email)
    if target is None:
        raise ToolError(f"No user with email {email}")
    old_role = target.role
    target.change_role(new_role)
    return {"email": target.email, "old_role": old_role, "new_role": new_role,
            "note": "Takes effect the next time this user logs in (new JWT)."}


# When: GET /api/users (Manage users panel), or the LLM picks it during /api/chat; needs the 'users:manage' scope.
# Use:  Returns every user with their permissions, plus the list of all scopes and each role's defaults.
# Who:  admin
@mcp.tool()
def list_users() -> dict:
    """List all users (learners, support, admins) with their role and permissions."""
    return {"users": [u.to_dict() for u in User.all()], "all_scopes": ALL_SCOPES, "role_defaults": ROLE_SCOPES}


# When: By create_user and update_user, before anything is saved.
# Use:  Rejects unknown roles, unknown scopes and short passwords; returns the scopes de-duplicated in standard order.
def validate_user_fields(role: str | None = None, scopes: list[str] | None = None,
                         password: str | None = None) -> list[str] | None:
    if role is not None and role not in ROLES:
        raise ToolError(f"role must be one of: {', '.join(ROLES)}")
    if scopes is not None:
        unknown = [sc for sc in scopes if sc not in ALL_SCOPES]
        if unknown:
            raise ToolError(f"Unknown permission(s): {', '.join(unknown)}")
    if password is not None and len(password) < MIN_PASSWORD_LENGTH:
        raise ToolError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return [sc for sc in ALL_SCOPES if sc in scopes] if scopes is not None else None


# When: POST /api/users (Manage users panel -> Add user), or the LLM picks it; needs the 'users:manage' scope.
# Use:  Creates a user. Leave `scopes` empty to give them their role's default permissions.
# Who:  admin
@mcp.tool()
def create_user(name: str, email: str, password: str, role: str, scopes: list[str] | None = None) -> dict:
    """Create a user. role: learner | support | admin. scopes: optional custom permissions (omit = role defaults)."""
    if not name.strip() or "@" not in email:
        raise ToolError("A name and a valid email are required")
    scopes = validate_user_fields(role, scopes, password)
    try:
        user = User.create(name, email, password, role, scopes)
    except sqlite3.IntegrityError:
        raise ToolError(f"A user with email {email.strip().lower()} already exists")
    return {"user": user.to_dict(), "message": "User created."}


# When: PUT /api/users/{user_id} (Manage users panel -> Edit), or the LLM picks it; needs the 'users:manage' scope.
# Use:  Updates only the fields that are passed; use_role_defaults=True drops custom permissions.
# Who:  admin
@mcp.tool()
def update_user(user_id: int, name: str | None = None, email: str | None = None, role: str | None = None,
                password: str | None = None, scopes: list[str] | None = None,
                use_role_defaults: bool = False) -> dict:
    """Update a user's name, email, role, password or permissions. Only the fields you pass are changed.
    scopes = custom permission list; use_role_defaults=True resets permissions to the role's defaults.
    Changes take effect the next time the user logs in."""
    user = User.get(user_id)
    if user is None:
        raise ToolError(f"User #{user_id} not found")
    scopes = validate_user_fields(role, scopes, password)

    new_scopes = None if use_role_defaults else (" ".join(scopes) if scopes is not None else user.scopes)
    if user.id == current_user()["id"] and (
        (role is not None and role != user.role) or new_scopes != user.scopes
    ):
        raise ToolError("You cannot change your own role or permissions")

    if name is not None:
        if not name.strip():
            raise ToolError("name cannot be empty")
        user.name = name.strip()
    if email is not None:
        if "@" not in email:
            raise ToolError("email is not valid")
        user.email = email.strip().lower()
    if role is not None:
        user.role = role
    if password:
        user.password_hash = hash_password(password)
    user.scopes = new_scopes
    try:
        user.save()
    except sqlite3.IntegrityError:
        raise ToolError(f"A user with email {user.email} already exists")
    return {"user": user.to_dict(), "message": "User updated. Changes apply at their next login."}


if __name__ == "__main__":
    mcp.run("streamable-http", host="127.0.0.1", port=MCP_PORT)