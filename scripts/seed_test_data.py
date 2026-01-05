#!/usr/bin/env python3
"""
Seed test data for E2E tests.

This script creates test users required for E2E authentication tests.
Run with: PYTHONPATH=src python scripts/seed_test_data.py
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentfx.api.core.security import get_password_hash
from agentfx.api.models.user import User, UserRole
from agentfx.api.core.database import Base


def get_database_url():
    """Get database URL from environment or use test default."""
    return os.getenv(
        "DATABASE_URL",
        "postgresql://test_user:test_password@localhost:5432/agentfx_test",
    )


def seed_test_users(db_url: str):
    """Create test users for E2E tests."""
    engine = create_engine(db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        # Test users matching E2E test expectations
        test_users = [
            {
                "email": "test@example.com",
                "password": "TestPass123!",
                "full_name": "Test User",
                "role": UserRole.VIEWER,
                "is_active": True,
            },
            {
                "email": "viewer@example.com",
                "password": "secretpassword",
                "full_name": "Test Viewer",
                "role": UserRole.VIEWER,
                "is_active": True,
            },
            {
                "email": "trader@example.com",
                "password": "traderpassword",
                "full_name": "Test Trader",
                "role": UserRole.TRADER,
                "is_active": True,
            },
            {
                "email": "admin@example.com",
                "password": "adminpassword",
                "full_name": "Test Admin",
                "role": UserRole.ADMIN,
                "is_active": True,
            },
            {
                "email": "inactive@example.com",
                "password": "inactivepassword",
                "full_name": "Inactive User",
                "role": UserRole.VIEWER,
                "is_active": False,
            },
        ]

        created_count = 0
        skipped_count = 0

        for user_data in test_users:
            # Check if user already exists
            existing = db.query(User).filter(User.email == user_data["email"]).first()
            if existing:
                print(f"  Skipped (exists): {user_data['email']}")
                skipped_count += 1
                continue

            # Create new user
            user = User(
                email=user_data["email"],
                hashed_password=get_password_hash(user_data["password"]),
                full_name=user_data["full_name"],
                role=user_data["role"],
                is_active=user_data["is_active"],
            )
            db.add(user)
            print(f"  Created: {user_data['email']} (role: {user_data['role'].value})")
            created_count += 1

        db.commit()
        print(f"\nSummary: {created_count} created, {skipped_count} skipped")

    except Exception as e:
        db.rollback()
        print(f"Error seeding data: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    db_url = get_database_url()
    print(f"Seeding test data to: {db_url}")
    print("-" * 50)
    seed_test_users(db_url)
    print("-" * 50)
    print("Done!")
