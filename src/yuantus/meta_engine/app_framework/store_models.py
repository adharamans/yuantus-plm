"""
App Store & Licensing Models
"""

from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import JSONB

from yuantus.models.base import Base


class MarketplaceAppListing(Base):
    """
    Local cache of available apps in the remote store.
    """

    __tablename__ = "meta_store_listings"

    id = Column(String, primary_key=True)
    name = Column(String(100), index=True)  # "plm.ecm"
    latest_version = Column(String(50))

    display_name = Column(String(200))
    description = Column(Text)
    category = Column(String(50))  # Core, Extension, Industry

    price_model = Column(String(50))  # Free, Subscription
    price_amount = Column(Integer)  # In cents

    icon_url = Column(String(500))
    publisher = Column(String(100))

    last_synced_at = Column(DateTime, default=datetime.utcnow)


class AppLicense(Base):
    """
    Entitlement for an installed app.
    """

    __tablename__ = "meta_app_licenses"

    id = Column(String, primary_key=True)
    app_registry_id = Column(
        String, ForeignKey("meta_app_registry.id"), nullable=True
    )  # Linked if installed
    app_name = Column(String(100), nullable=False)  # e.g. "plm.ecm"

    license_key = Column(String(100), unique=True, nullable=False)
    plan_type = Column(String(50))  # "Pro", "Enterprise"

    issued_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)  # Null = perpetual

    status = Column(String(20), default="Active")  # Active, Expired, Revoked

    # Use variant for SQLite compatibility
    license_data = Column(JSON().with_variant(JSONB, "postgresql"), default={})

    # PLM-COLLAB-P1-A (D0-3): entitlement tenant/org scoping. Nullable at the DB
    # layer (no backfill); the store_service resolver + tenant-scoped query enforce
    # that a legacy NULL-tenant license never silently unlocks for a resolved
    # tenant. org_id is recorded only -- NOT an entitlement filter in P1-A.
    tenant_id = Column(String(64), nullable=True, index=True)
    org_id = Column(String(64), nullable=True, index=True)
