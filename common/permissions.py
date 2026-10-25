"""
permissions.py - which SCOPES (permissions) each ROLE gets.

A scope is a "resource:action" string, like "tickets:read" or "certificates:issue".
At login the backend copies the user's scopes into the JWT ("scope" claim).
The MCP server then only looks at the scopes in the token - never at this table -
so a token is all it needs to decide what the caller may do.
"""

LEARNER_SCOPES = [
    "faq:read",           # search the FAQ
    "courses:read",       # list courses
    "courses:purchase",   # buy a course (simulated payment)
    "me:read",           # my enrollments, payments, certificates, tickets
    "tickets:read",       # open a ticket (only MY tickets - see tickets:read_all)
    "tickets:create",     # raise a ticket
    "tickets:reply",      # reply to MY ticket
]

SUPPORT_SCOPES = [
    "faq:read",
    "courses:read",
    "tickets:read",
    "tickets:read_all",   # open ANY ticket, see internal notes, list open tickets
    "tickets:manage",     # assign, reply (incl. internal notes), change status
    "learners:read",      # search learners, see a learner's details
    "access:extend",      # extend course access (max 30 days)
    "enrollments:remove", # remove a learner from a course
]

ADMIN_SCOPES = SUPPORT_SCOPES + [
    "access:extend_unlimited",  # no 30-day limit
    "certificates:issue",
    "faq:write",
    "stats:read",
    "users:manage",             # change roles
]

ROLE_SCOPES = {
    "learner": LEARNER_SCOPES,
    "support": SUPPORT_SCOPES,
    "admin": ADMIN_SCOPES,
}

# Every scope that exists, in display order. An admin can give a user any of these
# individually (custom permissions) instead of the role defaults above.
ALL_SCOPES = list(dict.fromkeys(LEARNER_SCOPES + ADMIN_SCOPES))


# When: At login (to build the JWT) and whenever a user is shown in the "Manage users" panel.
# Use:  Returns the user's permissions: their custom list if an admin set one, otherwise their role's defaults.
def effective_scopes(role: str, custom_scopes: str | None) -> list[str]:
    return custom_scopes.split() if custom_scopes is not None else list(ROLE_SCOPES[role])