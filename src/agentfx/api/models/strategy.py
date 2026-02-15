from sqlalchemy import JSON, Boolean, Column, DateTime, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class Strategy(Base):
    """
    Model representing a trading strategy definition.
    """

    __tablename__ = "strategies"

    id = Column(
        String, primary_key=True, index=True
    )  # Format: strategy_<name>_<version>
    name = Column(String, nullable=False, index=True)
    version = Column(String, nullable=False)  # Semantic versioning: MAJOR.MINOR.PATCH
    description = Column(String, nullable=True)
    parameters_schema = Column(
        JSON, nullable=False
    )  # JSON Schema for parameter validation
    indicators = Column(JSON, nullable=True)  # List of required indicators
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    runs = relationship("Run", back_populates="strategy", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Strategy {self.id} - {self.name} v{self.version}>"
