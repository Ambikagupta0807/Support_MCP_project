"""
models.py - one class per table. ALL database access goes through these classes.

Each model is a dataclass whose fields match the columns in schema.sql.
  * classmethods  (User.get_by_email, Ticket.open_tickets, ...) -> read rows
  * create()      -> insert a row and return the new object
  * instance methods (enrollment.extend(30), ticket.assign_to(...)) -> update a row

Some models carry extra "joined" fields (e.g. Enrollment.course_title) that are
filled by queries that JOIN another table. They default to None.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import bcrypt

from common.permissions import effective_scopes
from database.db import get_conn, migrate, now_str, parse_ts

migrate()  # bring an older telusko.db up to date (adds the users.scopes column)


# =============================================================================
# Base class: tiny helpers shared by every model
# =============================================================================
class Model:
    # When: By model classmethods that fetch a single row (get, get_by_email, find, ...).
    # Use:  Runs a SELECT and turns the first row into a model object, or returns None.
    @classmethod
    def _one(cls, sql: str, params: tuple = ()):
        """Run a query and return ONE object (or None)."""
        with get_conn() as conn:
            row = conn.execute(sql, params).fetchone()
        return cls(**dict(row)) if row else None

    # When: By model classmethods that fetch many rows (for_user, all, open_tickets, ...).
    # Use:  Runs a SELECT and turns every row into a model object.
    @classmethod
    def _many(cls, sql: str, params: tuple = ()) -> list:
        """Run a query and return a LIST of objects."""
        with get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [cls(**dict(r)) for r in rows]

    # When: By create() and update methods (extend, assign_to, set_status, ...).
    # Use:  Runs an INSERT/UPDATE/DELETE and returns the new row id.
    @staticmethod
    def _execute(sql: str, params: tuple = ()) -> int:
        """Run an INSERT/UPDATE/DELETE. Returns the new row id (for INSERTs)."""
        with get_conn() as conn:
            return conn.execute(sql, params).lastrowid

    # When: When an MCP tool returns a model as JSON.
    # Use:  Converts the model to a dict, dropping empty (None) fields.
    def to_dict(self) -> dict:
        """Convert to a plain dict (what the MCP tools return as JSON)."""
        return {k: v for k, v in asdict(self).items() if v is not None}


# =============================================================================
# User
# =============================================================================
ROLES = ("learner", "support", "admin")


@dataclass
class User(Model):
    id: int
    name: str
    email: str
    password_hash: str
    role: str
    scopes: str | None = None   # custom permissions (space-separated); None = role defaults

    # When: By assign_ticket_to_me, to load the calling support/admin user.
    # Use:  Fetches a user by id.
    @classmethod
    def get(cls, user_id: int) -> User | None:
        return cls._one("SELECT * FROM users WHERE id = ?", (user_id,))

    # When: On login, and by the get_learner_details and change_user_role tools.
    # Use:  Fetches a user by email (case-insensitive).
    @classmethod
    def get_by_email(cls, email: str) -> User | None:
        return cls._one("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))

    # When: By the search_learners tool.
    # Use:  Finds learners whose name or email contains the search text.
    @classmethod
    def search_learners(cls, query: str) -> list[User]:
        """Learners whose name or email contains `query` (case-insensitive)."""
        like = f"%{query.strip()}%"
        return cls._many(
            "SELECT * FROM users WHERE role = 'learner' AND (name LIKE ? OR email LIKE ?) ORDER BY name", (like, like)
        )

    # When: By the assign_ticket tool.
    # Use:  Finds support/admin users whose name or email contains the search text.
    @classmethod
    def search_support(cls, query: str) -> list[User]:
        """Support/admin users whose name or email contains `query` (case-insensitive)."""
        like = f"%{query.strip()}%"
        return cls._many(
            "SELECT * FROM users WHERE role IN ('support', 'admin') AND (name LIKE ? OR email LIKE ?) ORDER BY name",
            (like, like),
        )

    # When: By the list_users tool (Manage users panel).
    # Use:  Returns every user: admins first, then support, then learners.
    @classmethod
    def all(cls) -> list[User]:
        return cls._many("SELECT * FROM users ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'support' THEN 1 ELSE 2 END, name")

    # When: By the create_user tool (Manage users panel -> Add user).
    # Use:  Hashes the password, inserts the user and returns it. scopes=None means "use the role defaults".
    @classmethod
    def create(cls, name: str, email: str, password: str, role: str, scopes: list[str] | None = None) -> User:
        user_id = cls._execute(
            "INSERT INTO users (name, email, password_hash, role, scopes) VALUES (?, ?, ?, ?, ?)",
            (name.strip(), email.strip().lower(), hash_password(password), role,
             " ".join(scopes) if scopes is not None else None),
        )
        return cls.get(user_id)

    # When: By the update_user tool (Manage users panel -> Edit).
    # Use:  Saves the user's name, email, role, password hash and custom permissions.
    def save(self) -> None:
        self._execute(
            "UPDATE users SET name = ?, email = ?, password_hash = ?, role = ?, scopes = ? WHERE id = ?",
            (self.name, self.email, self.password_hash, self.role, self.scopes, self.id),
        )

    # When: At login (to build the JWT) and by to_dict().
    # Use:  The user's permissions: custom ones if set, otherwise the role defaults.
    @property
    def effective_scopes(self) -> list[str]:
        return effective_scopes(self.role, self.scopes)

    # When: On POST /api/login.
    # Use:  Checks the typed password against the stored bcrypt hash.
    def check_password(self, plain: str) -> bool:
        return bcrypt.checkpw(plain.encode(), self.password_hash.encode())

    # When: By the change_user_role tool (admin only).
    # Use:  Saves the user's new role (takes effect on their next login).
    def change_role(self, new_role: str) -> None:
        self._execute("UPDATE users SET role = ? WHERE id = ?", (new_role, self.id))
        self.role = new_role

    # When: Whenever a user is returned (login response, MCP tools).
    # Use:  Converts the user to a dict (with their effective permissions) WITHOUT the password hash.
    def to_dict(self) -> dict:
        # Never leak the password hash
        return {"id": self.id, "name": self.name, "email": self.email, "role": self.role,
                "scopes": self.effective_scopes, "custom_scopes": self.scopes is not None}


# When: By User.create() and the update_user tool when a password is set.
# Use:  Returns the bcrypt hash of a plain-text password (the plain password is never stored).
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


# =============================================================================
# Course
# =============================================================================
@dataclass
class Course(Model):
    id: int
    title: str
    description: str
    price_inr: int
    access_days: int

    # When: By the list_courses tool.
    # Use:  Returns every course.
    @classmethod
    def all(cls) -> list[Course]:
        return cls._many("SELECT * FROM courses ORDER BY id")

    # When: By the purchase_course tool.
    # Use:  Fetches a course by id.
    @classmethod
    def get(cls, course_id: int) -> Course | None:
        return cls._one("SELECT * FROM courses WHERE id = ?", (course_id,))



# =============================================================================
# Enrollment
# =============================================================================
@dataclass
class Enrollment(Model):
    id: int
    user_id: int
    course_id: int
    enrolled_at: str
    access_valid_till: str
    progress_percent: int
    certificate_issued: int
    course_title: str | None = None   # joined from courses

    _SELECT = """SELECT e.*, c.title AS course_title
                 FROM enrollments e JOIN courses c ON c.id = e.course_id"""

    # When: By the extend_course_access and issue_certificate tools.
    # Use:  Fetches an enrollment (with course title) by id.
    @classmethod
    def get(cls, enrollment_id: int) -> Enrollment | None:
        return cls._one(f"{cls._SELECT} WHERE e.id = ?", (enrollment_id,))

    # When: By get_my_enrollments, check_certificate_status and get_learner_details.
    # Use:  Returns all enrollments of a user.
    @classmethod
    def for_user(cls, user_id: int) -> list[Enrollment]:
        return cls._many(f"{cls._SELECT} WHERE e.user_id = ? ORDER BY e.id", (user_id,))

    # When: By purchase_course, to check whether the learner already has the course.
    # Use:  Fetches a user's enrollment in a specific course.
    @classmethod
    def find(cls, user_id: int, course_id: int) -> Enrollment | None:
        return cls._one(f"{cls._SELECT} WHERE e.user_id = ? AND e.course_id = ?", (user_id, course_id))

    # When: By the purchase_course tool, when a learner buys a course they were never enrolled in.
    # Use:  Enrolls the user with access for `access_days` from now, 0% progress, and returns the enrollment.
    @classmethod
    def create(cls, user_id: int, course_id: int, access_days: int) -> Enrollment:
        enrollment_id = cls._execute(
            """INSERT INTO enrollments (user_id, course_id, enrolled_at, access_valid_till, progress_percent,
                                        certificate_issued)
               VALUES (?, ?, ?, ?, 0, 0)""",
            (user_id, course_id, now_str(), now_str(timedelta(days=access_days))),
        )
        return cls.get(enrollment_id)

    # When: By to_dict() when an enrollment is returned.
    # Use:  True if the course access end date has passed.
    @property
    def is_expired(self) -> bool:
        return parse_ts(self.access_valid_till) < datetime.now()

    # When: By the check_certificate_status tool.
    # Use:  Returns a human-readable certificate status (issued / eligible / not yet).
    def certificate_status(self) -> str:
        if self.certificate_issued:
            return "Certificate issued"
        if self.progress_percent >= 100:
            return "Eligible but NOT issued yet - raise a certificate ticket"
        return f"Not eligible yet - complete {100 - self.progress_percent}% more"

    # When: By the extend_course_access tool.
    # Use:  Pushes the access end date forward by `days` and saves it.
    def extend(self, days: int) -> None:
        """Extend access by `days` - from today if already expired, else from the current expiry."""
        start = max(parse_ts(self.access_valid_till), datetime.now())
        new_till = (start + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        self._execute("UPDATE enrollments SET access_valid_till = ? WHERE id = ?", (new_till, self.id))
        self.access_valid_till = new_till

    # When: By the remove_learner_from_course tool (support/admin).
    # Use:  Deletes this enrollment, so the learner no longer has the course (payments are kept).
    def delete(self) -> None:
        self._execute("DELETE FROM enrollments WHERE id = ?", (self.id,))

    # When: By the issue_certificate tool (admin only).
    # Use:  Marks the certificate as issued.
    def issue_certificate(self) -> None:
        self._execute("UPDATE enrollments SET certificate_issued = 1 WHERE id = ?", (self.id,))
        self.certificate_issued = 1

    # When: Whenever an enrollment is returned from an MCP tool.
    # Use:  Converts the enrollment to a dict and adds an 'access_expired' flag.
    def to_dict(self) -> dict:
        return {**super().to_dict(), "access_expired": self.is_expired}


# =============================================================================
# Payment
# =============================================================================
@dataclass
class Payment(Model):
    id: int
    user_id: int
    course_id: int
    amount_inr: int
    status: str
    paid_at: str
    reference: str | None
    course_title: str | None = None   # joined from courses

    _SELECT = """SELECT p.*, c.title AS course_title
                 FROM payments p JOIN courses c ON c.id = p.course_id"""

    # When: By Payment.create(), to return the new payment.
    # Use:  Fetches a payment (with course title) by id.
    @classmethod
    def get(cls, payment_id: int) -> Payment | None:
        return cls._one(f"{cls._SELECT} WHERE p.id = ?", (payment_id,))

    # When: By get_my_payments and get_learner_details.
    # Use:  Returns a user's payments, newest first.
    @classmethod
    def for_user(cls, user_id: int) -> list[Payment]:
        return cls._many(f"{cls._SELECT} WHERE p.user_id = ? ORDER BY p.paid_at DESC", (user_id,))

    # When: By the purchase_course tool, after the (simulated) payment succeeds.
    # Use:  Records a payment and returns it.
    @classmethod
    def create(cls, user_id: int, course_id: int, amount_inr: int, status: str, reference: str) -> Payment:
        payment_id = cls._execute(
            "INSERT INTO payments (user_id, course_id, amount_inr, status, paid_at, reference) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, course_id, amount_inr, status, now_str(), reference),
        )
        return cls.get(payment_id)

    # When: By the get_support_stats tool.
    # Use:  Counts and totals payments, grouped by status.
    @staticmethod
    def summary_by_status() -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count, SUM(amount_inr) AS total_inr FROM payments GROUP BY status"
            ).fetchall()
        return [dict(r) for r in rows]


# =============================================================================
# Ticket + TicketMessage
# =============================================================================
TICKET_CATEGORIES = ("access", "payment", "certificate", "technical", "other")
TICKET_PRIORITIES = ("low", "medium", "high")
TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")


@dataclass
class TicketMessage(Model):
    id: int
    ticket_id: int
    sender_id: int
    message: str
    is_internal: int
    created_at: str
    sender_name: str | None = None   # joined from users
    sender_role: str | None = None   # joined from users

    # When: By Ticket.messages().
    # Use:  Returns a ticket's messages; internal notes only if include_internal is True.
    @classmethod
    def for_ticket(cls, ticket_id: int, include_internal: bool) -> list[TicketMessage]:
        sql = """SELECT m.*, u.name AS sender_name, u.role AS sender_role
                 FROM ticket_messages m JOIN users u ON u.id = m.sender_id WHERE m.ticket_id = ?"""
        if not include_internal:
            sql += " AND m.is_internal = 0"
        return cls._many(sql + " ORDER BY m.id", (ticket_id,))

    # When: By Ticket.create() (first message) and Ticket.add_message() (replies).
    # Use:  Saves a new message and bumps the ticket's updated_at.
    @classmethod
    def create(cls, ticket_id: int, sender_id: int, message: str, internal: bool = False) -> int:
        msg_id = cls._execute(
            "INSERT INTO ticket_messages (ticket_id, sender_id, message, is_internal, created_at) VALUES (?, ?, ?, ?, ?)",
            (ticket_id, sender_id, message, int(internal), now_str()),
        )
        cls._execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now_str(), ticket_id))
        return msg_id


@dataclass
class Ticket(Model):
    id: int
    user_id: int
    subject: str
    category: str
    priority: str
    status: str
    assigned_to: int | None
    created_at: str
    updated_at: str
    learner_name: str | None = None       # joined from users
    learner_email: str | None = None      # joined from users
    assigned_to_name: str | None = None   # joined from users

    _SELECT = """SELECT t.*, u.name AS learner_name, u.email AS learner_email, a.name AS assigned_to_name
                 FROM tickets t JOIN users u ON u.id = t.user_id LEFT JOIN users a ON a.id = t.assigned_to"""

    # When: By load_ticket in the MCP server, and by Ticket.create() to return the new ticket.
    # Use:  Fetches a ticket (with learner and assignee names) by id.
    @classmethod
    def get(cls, ticket_id: int) -> Ticket | None:
        return cls._one(f"{cls._SELECT} WHERE t.id = ?", (ticket_id,))

    # When: By get_my_tickets and get_learner_details.
    # Use:  Returns all tickets raised by a user, newest first.
    @classmethod
    def for_user(cls, user_id: int) -> list[Ticket]:
        return cls._many(f"{cls._SELECT} WHERE t.user_id = ? ORDER BY t.id DESC", (user_id,))

    # When: By the list_open_tickets tool.
    # Use:  Returns open + in-progress tickets, highest priority first, optionally by category.
    @classmethod
    def open_tickets(cls, category: str | None = None) -> list[Ticket]:
        """Open + in_progress tickets, highest priority first."""
        sql = f"{cls._SELECT} WHERE t.status IN ('open', 'in_progress')"
        params: tuple = ()
        if category:
            sql += " AND t.category = ?"
            params = (category,)
        sql += " ORDER BY CASE t.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, t.created_at"
        return cls._many(sql, params)

    # When: By the raise_ticket tool.
    # Use:  Creates a ticket plus its first message and returns the new ticket.
    @classmethod
    def create(cls, user_id: int, subject: str, category: str, priority: str, message: str) -> Ticket:
        ticket_id = cls._execute(
            """INSERT INTO tickets (user_id, subject, category, priority, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?)""",
            (user_id, subject, category, priority, now_str(), now_str()),
        )
        TicketMessage.create(ticket_id, user_id, message)
        return cls.get(ticket_id)

    # When: By ticket_with_messages (get_ticket tool).
    # Use:  Returns this ticket's conversation.
    def messages(self, include_internal: bool) -> list[TicketMessage]:
        return TicketMessage.for_ticket(self.id, include_internal)

    # When: By the reply_to_my_ticket and reply_to_ticket tools.
    # Use:  Adds a reply (or an internal note) to this ticket.
    def add_message(self, sender_id: int, message: str, internal: bool = False) -> None:
        TicketMessage.create(self.id, sender_id, message, internal)

    # When: By the assign_ticket_to_me and assign_ticket tools.
    # Use:  Assigns the ticket to a support/admin user and marks it in_progress.
    def assign_to(self, user: User) -> None:
        self._execute("UPDATE tickets SET assigned_to = ?, status = 'in_progress', updated_at = ? WHERE id = ?",
                      (user.id, now_str(), self.id))
        self.assigned_to, self.assigned_to_name, self.status = user.id, user.name, "in_progress"

    # When: By the update_ticket_status tool.
    # Use:  Saves the ticket's new status.
    def set_status(self, status: str) -> None:
        self._execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?", (status, now_str(), self.id))
        self.status = status

    # When: By the get_support_stats tool.
    # Use:  Counts tickets grouped by status or by category.
    @staticmethod
    def count_by(column: str) -> list[dict]:
        """Ticket counts grouped by 'status' or 'category'."""
        if column not in ("status", "category"):   # never put user input straight into SQL
            raise ValueError("column must be 'status' or 'category'")
        with get_conn() as conn:
            rows = conn.execute(f"SELECT {column}, COUNT(*) AS count FROM tickets GROUP BY {column}").fetchall()
        return [dict(r) for r in rows]

    # When: By the get_support_stats tool.
    # Use:  Counts tickets assigned to each support agent.
    @staticmethod
    def count_per_agent() -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute("""SELECT u.name AS agent, COUNT(*) AS count FROM tickets t
                                   JOIN users u ON u.id = t.assigned_to GROUP BY u.name""").fetchall()
        return [dict(r) for r in rows]


# =============================================================================
# FAQ
# =============================================================================
@dataclass
class FAQ(Model):
    id: int
    question: str
    answer: str
    category: str

    # When: By FAQ.search().
    # Use:  Returns every FAQ.
    @classmethod
    def all(cls) -> list[FAQ]:
        return cls._many("SELECT * FROM faqs ORDER BY id")

    # When: By the search_faq tool.
    # Use:  Keyword search: returns the FAQs that match the most query words.
    @classmethod
    def search(cls, query: str, limit: int = 3) -> list[FAQ]:
        """Simple keyword search: rank FAQs by how many query words they contain."""
        words = [w for w in query.lower().split() if len(w) > 2] or [query.lower()]
        scored = []
        for faq in cls.all():
            text = f"{faq.question} {faq.answer} {faq.category}".lower()
            score = sum(w in text for w in words)
            if score:
                scored.append((score, faq))
        scored.sort(key=lambda s: -s[0])
        return [faq for _, faq in scored[:limit]]

    # When: By the add_faq tool (admin only).
    # Use:  Inserts a new FAQ and returns it.
    @classmethod
    def create(cls, question: str, answer: str, category: str) -> FAQ:
        faq_id = cls._execute("INSERT INTO faqs (question, answer, category) VALUES (?, ?, ?)",
                              (question, answer, category))
        return cls(faq_id, question, answer, category)