"""
Version Control Models
Aras-style Versioning: Generation (1,2,3) + Revision (A,B,C)

Sprint 2 Enhancement: Version-File Integration
"""

from datetime import datetime
from enum import Enum
from sqlalchemy import (
    Column,
    ForeignKey,
    String,
    Integer,
    Boolean,
    DateTime,
    Text,
    UniqueConstraint,
    Index,
    JSON,
    false,
    true,
    text,
)
from sqlalchemy.orm import relationship, foreign
from sqlalchemy.dialects.postgresql import JSONB

from yuantus.models.base import Base
from yuantus.meta_engine.models.file import FileContainer


class VersionFileRole(str, Enum):
    """File roles within a version."""

    NATIVE_CAD = "native_cad"
    PREVIEW = "preview"
    GEOMETRY = "geometry"
    DRAWING = "drawing"
    ATTACHMENT = "attachment"
    REFERENCE = "reference"


class ItemVersion(Base):
    """
    Item Version Snapshot.
    Groups a specific revision of an Item.
    """

    __tablename__ = "meta_item_versions"

    id = Column(String, primary_key=True)

    # Parent Item
    item_id = Column(
        String,
        ForeignKey("meta_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Dual-track Versioning
    # Generation: Major version (1, 2, 3...)
    generation = Column(Integer, default=1)
    # Revision: Minor version (A, B... Z, AA...)
    revision = Column(String(10), default="A")

    # Display Label: "1.A", "2.C"
    version_label = Column(String(50))

    # Lifecycle State specific to this version
    state = Column(String(50), default="Draft")

    # Flags
    is_current = Column(
        Boolean, nullable=False, server_default=true(), default=True, index=True
    )
    is_released = Column(
        Boolean, nullable=False, server_default=false(), default=False, index=True
    )
    # B1: version replacement signal. Set True on the immediate prior released
    # version when a newer version on the same line is released
    # (VersionService.release). "active released" = is_released AND NOT is_superseded.
    is_superseded = Column(
        Boolean, nullable=False, server_default=false(), default=False, index=True
    )

    # Release Info
    released_at = Column(DateTime, nullable=True)
    released_by_id = Column(Integer, ForeignKey("rbac_users.id"), nullable=True)

    # Checkout / Locking
    checked_out_by_id = Column(Integer, ForeignKey("rbac_users.id"), nullable=True)
    checked_out_at = Column(DateTime, nullable=True)

    # Version Chain
    predecessor_id = Column(String, ForeignKey("meta_item_versions.id"), nullable=True)

    # Branching
    branch_name = Column(
        String(100), nullable=False, server_default="main", default="main"
    )
    branched_from_id = Column(
        String, ForeignKey("meta_item_versions.id"), nullable=True
    )

    # Data Snapshot (Full properties dump)
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("rbac_users.id"), nullable=True)

    # Sprint 2: File tracking fields
    file_count = Column(Integer, default=0)
    primary_file_id = Column(String, nullable=True)
    thumbnail_data = Column(Text, nullable=True)  # Base64 encoded thumbnail

    # B1 (D4b): concurrent-revision guard at the DB level. At most ONE open
    # (unreleased) current version per item line + branch — makes a concurrent
    # double-revise race impossible (the app-guard in revise()/new_generation()
    # gives the friendly early 400; this is the race-proof backstop). COALESCE keeps
    # it NULL-safe even if a legacy row slipped a NULL branch_name (columns are also
    # NOT NULL + server_default). Verified to build+enforce via create_all on SQLite.
    __table_args__ = (
        Index(
            "uq_itemversion_open_current_per_line",
            "item_id",
            text("coalesce(branch_name, 'main')"),
            unique=True,
            sqlite_where=text("is_current = 1 AND is_released = 0"),
            postgresql_where=text("is_current IS TRUE AND is_released IS NOT TRUE"),
        ),
    )

    # Relationships
    # item = relationship("Item", back_populates="versions") # Defined in Item

    history = relationship(
        "VersionHistory",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="VersionHistory.created_at",
    )

    # Sprint 2: Version files relationship
    version_files = relationship(
        "VersionFile",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="VersionFile.sequence",
    )

    # Self-referential
    predecessor = relationship(
        "ItemVersion", remote_side=[id], foreign_keys=[predecessor_id]
    )
    branched_from = relationship(
        "ItemVersion", remote_side=[id], foreign_keys=[branched_from_id]
    )


class VersionHistory(Base):
    """
    Audit Log for Version Operations.
    """

    __tablename__ = "meta_version_history"

    id = Column(String, primary_key=True)
    version_id = Column(
        String, ForeignKey("meta_item_versions.id", ondelete="CASCADE"), index=True
    )

    action = Column(String(50))  # create, checkout, checkin, revise...
    user_id = Column(Integer, ForeignKey("rbac_users.id"))
    comment = Column(Text, nullable=True)

    # Changeset (JSON diff)
    changes = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    version = relationship("ItemVersion", back_populates="history")
    user = relationship("yuantus.security.rbac.models.RBACUser")


class VersionFile(Base):
    """
    Version-File Association.
    Links files to specific versions with role and ordering.

    Sprint 2 Enhancement: Enables file snapshots per version.
    """

    __tablename__ = "meta_version_files"

    id = Column(String, primary_key=True)

    # Parent Version
    version_id = Column(
        String,
        ForeignKey("meta_item_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Associated File
    file_id = Column(String, nullable=False, index=True)

    # File Role (native_cad, preview, geometry, attachment, etc.)
    file_role = Column(String, default="attachment", index=True)

    # Display sequence
    sequence = Column(Integer, default=0)

    # Snapshot path (copy of file path at time of version creation)
    snapshot_path = Column(String, nullable=True)

    # Is this the primary/main file?
    is_primary = Column(Boolean, default=False)

    # WP1.3 CAD 2D/3D staleness mirror (frozen snapshot of the ItemFile fields at
    # the time this version snapshot was created/synced). ItemFile is the
    # current-state authority; recompute only writes the *current* version's rows.
    import_batch_id = Column(String, nullable=True, index=True)
    source_batch_id = Column(String, nullable=True)
    needs_update = Column(Boolean, nullable=False, default=False)
    staleness_reason = Column(String, nullable=True)
    staleness_checked_at = Column(DateTime(timezone=True), nullable=True)

    # File-level checkout / locking
    checked_out_by_id = Column(Integer, ForeignKey("rbac_users.id"), nullable=True)
    checked_out_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    version = relationship("ItemVersion", back_populates="version_files")
    file = relationship(
        FileContainer,
        primaryjoin=lambda: foreign(VersionFile.file_id) == FileContainer.id,
        viewonly=True,
    )

    # Unique constraint: one file per version per role
    __table_args__ = (
        Index(
            "uq_version_file_role", "version_id", "file_id", "file_role", unique=True
        ),
    )


class ItemIteration(Base):
    """
    Lightweight Iteration within a Version.

    Sprint 4 Enhancement: Allows work-in-progress saves without creating
    formal revisions. Iterations are like "auto-saves" or "checkpoints"
    within a version.

    Use Cases:
    - Auto-save during long CAD sessions
    - Pre-release checkpoints
    - Internal review iterations before formal revision

    Structure:
    - Version 1.A
      - Iteration 1 (initial)
      - Iteration 2 (WIP save)
      - Iteration 3 (current WIP)
    """

    __tablename__ = "meta_item_iterations"

    id = Column(String, primary_key=True)

    # Parent Version
    version_id = Column(
        String,
        ForeignKey("meta_item_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Iteration sequence within version (1, 2, 3...)
    iteration_number = Column(Integer, default=1, nullable=False)

    # Label: "1.A.1", "1.A.2"
    iteration_label = Column(String(50))

    # Is this the current/latest iteration?
    is_latest = Column(Boolean, default=True, index=True)

    # Data snapshot
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    description = Column(Text, nullable=True)

    # Source of this iteration
    source_type = Column(String(20), default="manual")  # manual, auto_save, import

    # Audit
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("rbac_users.id"), nullable=True)

    # File associations for this iteration
    file_count = Column(Integer, default=0)
    primary_file_id = Column(String, nullable=True)

    # Relationships
    version = relationship("ItemVersion", backref="iterations")

    __table_args__ = (
        UniqueConstraint("version_id", "iteration_number", name="uq_version_iteration"),
        Index("ix_iteration_version_latest", "version_id", "is_latest"),
    )


class RevisionScheme(Base):
    """
    Revision Numbering Scheme Configuration.

    Sprint 4 Enhancement: Allows per-ItemType or global configuration
    of revision numbering strategy.

    Schemes:
    - letter: A, B, C, ..., Z, AA, AB, ... (default, Excel-style)
    - number: 1, 2, 3, ... (numeric)
    - hybrid: A1, A2, ..., A99, B1, ... (letter + number)
    """

    __tablename__ = "meta_revision_schemes"

    id = Column(String, primary_key=True)

    # Scheme name
    name = Column(String(100), nullable=False)

    # Scheme type: letter, number, hybrid
    scheme_type = Column(String(20), default="letter", nullable=False)

    # Initial revision value
    initial_revision = Column(String(10), default="A")

    # For hybrid schemes
    max_number_before_rollover = Column(Integer, default=99)

    # Associated ItemType (NULL = global default)
    item_type_id = Column(
        String,
        ForeignKey("meta_item_types.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Is this the default scheme?
    is_default = Column(Boolean, default=False, index=True)

    # Description
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_revision_scheme_itemtype", "item_type_id", "is_default"),
    )
