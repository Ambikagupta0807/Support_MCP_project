# Multi-Role AI Support Agent

An AI-powered support assistant for an online learning platform, where a single LLM
agent serves three different user roles — each with a different, server-enforced set
of permissions.

## What it does

- Learners can check their enrollments, buy courses, raise and reply to their own
  support tickets, and check certificate status
- Support staff can view and manage tickets across all learners, search learner
  profiles, and extend course access (within a role-based limit)
- Admins can manage users, change roles, issue certificates, and view support
  analytics — with no limit on their permissions

## Architecture

- A FastAPI backend handles login (issuing JWTs with role-based scopes) and runs the
  LLM tool-calling loop
- A separate MCP (Model Context Protocol) server exposes 19 tools, each requiring a
  specific scope (e.g. `tickets:manage`, `users:manage`)
- Authorization happens in two layers: tool-level (can you even call this tool?) and
  data-level (can you access THIS specific record — e.g. a learner can only see
  their own tickets, even with a valid token)
- The MCP server filters the tool list itself, so a learner's AI literally never sees
  admin tools exist — not just a UI restriction, a server-enforced one

## Safety design

- The LLM must pass both an ID and a matching title before a purchase or removal
  goes through, preventing a hallucinated ID from acting on the wrong record
- Admins cannot change their own role
- Destructive actions require explicit user confirmation before the LLM calls the tool

## Tech stack

Python · LangChain · MCP (Model Context Protocol) · JWT · FastAPI · SQLite

## Key features

- Role-based tool visibility (not just UI hiding — the server filters what's exposed)
- Scope-based authorization enforced per tool call
- Data-level access control (own-record-only for learners)
- Real transactional actions (purchases, ticket assignment) with built-in safety checks
