import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import HTTPException

COOKIE = "cutlist_session"


def set_cookie(response, token):
    response.set_cookie(COOKIE, token, httponly=True, secure=os.environ.get("CUTLIST_SECURE_COOKIE") == "true", samesite="strict", max_age=30 * 86400, path="/")


def verify_invite(value):
    required = os.environ.get("CUTLIST_ACCESS_KEY", "")
    if required and (not isinstance(value, str) or not hmac.compare_digest(value, required)):
        raise HTTPException(401, "Enter the workspace invitation key to continue.")
