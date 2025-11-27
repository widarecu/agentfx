#!/usr/bin/env python
"""Generate Argon2 hash for test password"""

from src.agentfx.api.core.security import get_password_hash

password = "TestPass123!"
hashed = get_password_hash(password)
print(f"Password: {password}")
print(f"Hash: {hashed}")
