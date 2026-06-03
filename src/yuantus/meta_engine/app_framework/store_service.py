"""
App Store Service
Simulates interaction with a remote PLM App Store.
"""

from typing import List, Dict, Any
from sqlalchemy.orm import Session
import uuid
from datetime import datetime
from yuantus.meta_engine.app_framework.license_scope import resolve_license_scope
from yuantus.meta_engine.app_framework.store_models import (
    MarketplaceAppListing,
    AppLicense,
)
from yuantus.meta_engine.app_framework.service import AppService


class AppStoreService:
    def __init__(self, session: Session):
        self.session = session
        self.app_service = AppService(session)

    def sync_store_listings(self):
        """
        Mock: Fetch from remote and update local cache.
        """
        # Mock Remote Data
        mock_remote_apps = [
            {
                "id": "app_pm",
                "name": "plm.pm",
                "latest_version": "1.2.0",
                "display_name": "Project Management",
                "description": "Gantt charts and resource planning.",
                "category": "Extension",
                "price_model": "Subscription",
                "price_amount": 1000,
                "publisher": "PLM Corp",
            },
            {
                "id": "app_qms",
                "name": "plm.qms",
                "latest_version": "1.0.5",
                "display_name": "Quality Management",
                "description": "NCMR, CAPA, Audit.",
                "category": "Core",
                "price_model": "Free",
                "price_amount": 0,
                "publisher": "PLM Corp",
            },
        ]

        # Upsert
        for data in mock_remote_apps:
            listing = (
                self.session.query(MarketplaceAppListing)
                .filter_by(name=data["name"])
                .first()
            )
            if not listing:
                listing = MarketplaceAppListing(id=data["id"])
                self.session.add(listing)

            listing.name = data["name"]
            listing.latest_version = data["latest_version"]
            listing.display_name = data["display_name"]
            listing.description = data["description"]
            listing.category = data["category"]
            listing.price_model = data["price_model"]
            listing.price_amount = data["price_amount"]
            listing.publisher = data["publisher"]
            listing.last_synced_at = datetime.utcnow()

    def search_apps(
        self, query: str = None, category: str = None
    ) -> List[MarketplaceAppListing]:
        q = self.session.query(MarketplaceAppListing)
        if query:
            q = q.filter(MarketplaceAppListing.display_name.ilike(f"%{query}%"))
        if category:
            q = q.filter(MarketplaceAppListing.category == category)
        return q.all()

    def purchase_app(
        self, listing_id: str, plan_type: str = "Standard", user_id: int = 1
    ) -> AppLicense:
        """
        Simulate purchase/obtaining a license.
        """
        listing = self.session.query(MarketplaceAppListing).get(listing_id)
        if not listing:
            raise ValueError("App not found in store")

        # Create License (PLM-COLLAB-P1-A: scoped to the resolved tenant)
        tenant_id, org_id = resolve_license_scope()
        lic_key = str(uuid.uuid4()).upper()
        license = AppLicense(
            id=str(uuid.uuid4()),
            app_name=listing.name,
            license_key=lic_key,
            plan_type=plan_type,
            status="Active",
            issued_at=datetime.utcnow(),
            tenant_id=tenant_id,
            org_id=org_id,
        )
        self.session.add(license)
        return license

    def install_from_store(self, listing_id: str, user_id: int) -> Dict[str, Any]:
        """
        1. Verify License (if not free)
        2. Download Manifest (Mock)
        3. Register App
        """
        listing = self.session.query(MarketplaceAppListing).get(listing_id)
        if not listing:
            raise ValueError("App not found")

        # Check license (PLM-COLLAB-P1-A: only a license scoped to the resolved
        # tenant unlocks; a legacy NULL-tenant license never matches).
        if listing.price_model != "Free":
            tenant_id, _ = resolve_license_scope()
            lic = (
                self.session.query(AppLicense)
                .filter_by(app_name=listing.name, status="Active", tenant_id=tenant_id)
                .first()
            )
            if not lic:
                raise ValueError("No active license found. Purchase first.")

        # Mock Download Manifest
        manifest = self._fetch_manifest(listing.name, listing.latest_version)

        # Register
        app_reg = self.app_service.register_app(manifest, installer_id=user_id)

        return {"status": "Installed", "app_id": app_reg.id}

    def _fetch_manifest(self, app_name: str, version: str) -> Dict[str, Any]:
        """Mock remote fetch"""
        return {
            "name": app_name,
            "version": version,
            "display_name": f"Installed {app_name}",
            "description": "Downloaded from Store",
            "extensions": [],  # Empty for mock
        }
