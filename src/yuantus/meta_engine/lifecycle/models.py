import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Boolean, ForeignKey, Integer, false, DateTime, Text, JSON, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import Optional, List

from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACRole  # Import RBACRole

# Ensure workflow meta tables are registered in Base.metadata.
# LifecycleState has FK to meta_workflow_maps; without importing workflow models,
# SQLAlchemy create_all() will fail in tests.
from yuantus.meta_engine.workflow import models as _workflow_models  # noqa: F401


class LifecycleMap(Base):
    """
    生命周期图定义
    """

    __tablename__ = "meta_lifecycle_maps"
    __table_args__ = {"extend_existing": True}
    id = Column(String, primary_key=True)
    name = Column(String)
    description = Column(String, nullable=True)

    states = relationship("LifecycleState", back_populates="lifecycle_map")
    transitions = relationship("LifecycleTransition", back_populates="lifecycle_map")


class LifecycleState(Base):
    """
    状态 (State)
    e.g. "Draft", "In Review", "Released"
    """

    __tablename__ = "meta_lifecycle_states"
    __table_args__ = {"extend_existing": True}
    id = Column(String, primary_key=True)
    lifecycle_map_id = Column(String, ForeignKey("meta_lifecycle_maps.id"))

    name = Column(String, nullable=False)  # "Draft"
    label = Column(String(100))  # Display name
    sequence = Column(Integer, default=0)  # For UI ordering
    is_start_state = Column(Boolean, default=False)

    # State flags
    is_end_state = Column(Boolean, default=False)  # Final state (e.g. Obsolete)
    is_released = Column(Boolean, default=False)  # Does this state signify "Released"?
    is_suspended = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )  # Does this state block downstream consumption?

    # Behavior
    version_lock = Column(
        Boolean, default=False
    )  # If true, Item cannot be edited in this state

    permission_id = Column(String, ForeignKey("meta_permissions.id"), nullable=True)

    # 新增: 状态默认权限 (Phase 1.2)
    default_permission_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("meta_permissions.id", ondelete="SET NULL"),
        nullable=True,
        comment="进入此状态时Item获得的默认权限",
    )

    # Workflow Integration (Phase 2.5)
    workflow_map_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("meta_workflow_maps.id", ondelete="SET NULL"),
        nullable=True,
        comment="进入此状态时自动触发的工作流",
    )

    # 新增: 身份-权限映射 (Phase 1.2)
    identity_permissions: Mapped[List["StateIdentityPermission"]] = relationship(
        "StateIdentityPermission", back_populates="state", cascade="all, delete-orphan"
    )

    lifecycle_map = relationship("LifecycleMap", back_populates="states")


class LifecycleTransition(Base):
    """
    状态流转 (Transition)
    定义 Draft -> Review 需要什么条件/权限
    """

    __tablename__ = "meta_lifecycle_transitions"
    __table_args__ = {"extend_existing": True}
    id = Column(String, primary_key=True)
    lifecycle_map_id = Column(String, ForeignKey("meta_lifecycle_maps.id"))

    from_state_id = Column(String, ForeignKey("meta_lifecycle_states.id"))
    to_state_id = Column(String, ForeignKey("meta_lifecycle_states.id"))

    action_name = Column(String(50))  # Trigger action name

    role_allowed_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("rbac_roles.id", ondelete="SET NULL"), nullable=True
    )
    role_allowed: Mapped[Optional["RBACRole"]] = relationship(
        "RBACRole", foreign_keys=[role_allowed_id]
    )

    # Text-based JSON condition DSL (Phase 2.2)
    condition = Column(String, nullable=True)

    lifecycle_map = relationship("LifecycleMap", back_populates="transitions")


class StateIdentityPermission(Base):
    """状态-身份-权限映射表 (Phase 1.2)"""

    __tablename__ = "meta_state_identity_permissions"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    state_id: Mapped[str] = mapped_column(
        ForeignKey("meta_lifecycle_states.id", ondelete="CASCADE")
    )

    # 可以是动态身份或角色ID
    identity_type: Mapped[str] = mapped_column(String(20), comment="dynamic|role")
    identity_value: Mapped[str] = mapped_column(
        String(50), comment="动态身份名或角色ID"
    )

    # 权限
    can_read: Mapped[bool] = mapped_column(default=True)
    can_update: Mapped[bool] = mapped_column(default=False)
    can_delete: Mapped[bool] = mapped_column(default=False)
    can_promote: Mapped[bool] = mapped_column(default=False)

    state: Mapped["LifecycleState"] = relationship(
        "LifecycleState", back_populates="identity_permissions"
    )


class LifecycleTransitionHistory(Base):
    """Durable audit record of a SUCCESSFUL lifecycle state transition.

    Written once per committed promote() (best-effort: a write failure never fails the
    transition). Records the state move, the state-driven permission move (#808), the actor,
    and the promote() comment. ``outcome`` is always "success" in v1 — reserved so a future
    all-attempts slice can record denied/failed attempts without a schema change.

    ``actor_user_id`` is intentionally FK-free (a system/automated promote may run with an
    unvalidated user id, e.g. 0); ``item_id`` is a recorded value (no FK) so the audit row
    survives item deletion (immutable history), mirroring DateObsoleteImpact.
    """

    __tablename__ = "meta_lifecycle_transition_history"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_id = Column(String, nullable=False, index=True)
    from_state_id = Column(String, nullable=True)
    from_state_name = Column(String, nullable=True)
    to_state_id = Column(String, nullable=True)
    to_state_name = Column(String, nullable=True)
    from_permission_id = Column(String, nullable=True)
    to_permission_id = Column(String, nullable=True)
    transition_id = Column(String, nullable=True)
    lifecycle_map_id = Column(String, nullable=True)
    actor_user_id = Column(Integer, nullable=True)  # no FK: system/automated promotes
    comment = Column(Text, nullable=True)
    outcome = Column(String(20), nullable=False, default="success", server_default="success")
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index(
            "ix_meta_lifecycle_transition_history_item_created",
            "item_id",
            "created_at",
        ),
    )
