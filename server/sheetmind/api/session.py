"""Anonymous-session request helpers."""

from fastapi import Request


def current_session_id(request: Request) -> str:
    return request.state.anonymous_session_id
