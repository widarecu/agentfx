#!/usr/bin/env python3
"""
Seed test data for E2E tests.

This script creates test users required for E2E authentication tests.

Usage:
    # Seed all test users
    PYTHONPATH=src python scripts/seed_test_data.py

    # Promote a specific user to TRADER role
    PYTHONPATH=src python scripts/seed_test_data.py --promote-to-trader user@example.com

    # Promote a user to ADMIN role
    PYTHONPATH=src python scripts/seed_test_data.py --promote-to-admin user@example.com
"""

import argparse
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentfx.api.core.database import Base
from agentfx.api.core.security import get_password_hash
from agentfx.api.models.user import User, UserRole


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
        updated_count = 0

        for user_data in test_users:
            # Check if user already exists
            existing = db.query(User).filter(User.email == user_data["email"]).first()
            if existing:
                # Update existing user to ensure correct password and settings
                existing.hashed_password = get_password_hash(user_data["password"])
                existing.full_name = user_data["full_name"]
                existing.role = user_data["role"]
                existing.is_active = user_data["is_active"]
                print(
                    f"  Updated: {user_data['email']} (role: {user_data['role'].value})"
                )
                updated_count += 1
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
        print(f"\nSummary: {created_count} created, {updated_count} updated")

    except Exception as e:
        db.rollback()
        print(f"Error seeding data: {e}")
        raise
    finally:
        db.close()


def promote_user(db_url: str, email: str, target_role: UserRole) -> bool:
    """
    Promote a user to a specified role.

    Args:
        db_url: Database connection URL
        email: Email of the user to promote
        target_role: The role to assign (TRADER or ADMIN)

    Returns:
        True if successful, False if user not found
    """
    engine = create_engine(db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        user = db.query(User).filter(User.email == email).first()

        if not user:
            print(f"Error: User with email '{email}' not found")
            return False

        old_role = user.role.value
        user.role = target_role
        db.commit()

        print(f"Success: {email} promoted from {old_role} to {target_role.value}")
        return True

    except Exception as e:
        db.rollback()
        print(f"Error promoting user: {e}")
        raise
    finally:
        db.close()


def main():
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="Seed test data or manage user roles for AgentFX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Seed all test users
  PYTHONPATH=src python scripts/seed_test_data.py

  # Promote a user to TRADER (can create optimization jobs)
  PYTHONPATH=src python scripts/seed_test_data.py --promote-to-trader user@example.com

  # Promote a user to ADMIN (full access)
  PYTHONPATH=src python scripts/seed_test_data.py --promote-to-admin user@example.com
        """,
    )

    parser.add_argument(
        "--promote-to-trader",
        metavar="EMAIL",
        help="Promote a user to TRADER role (can create optimization jobs)",
    )

    parser.add_argument(
        "--promote-to-admin",
        metavar="EMAIL",
        help="Promote a user to ADMIN role (full access)",
    )

    args = parser.parse_args()
    db_url = get_database_url()

    if args.promote_to_trader:
        print(f"Database: {db_url}")
        print("-" * 50)
        success = promote_user(db_url, args.promote_to_trader, UserRole.TRADER)
        sys.exit(0 if success else 1)

    elif args.promote_to_admin:
        print(f"Database: {db_url}")
        print("-" * 50)
        success = promote_user(db_url, args.promote_to_admin, UserRole.ADMIN)
        sys.exit(0 if success else 1)

    else:
        # Default: seed test users
        print(f"Seeding test data to: {db_url}")
        print("-" * 50)
        seed_test_users(db_url)
        print("-" * 50)
        print("Done!")


if __name__ == "__main__":
    main()
