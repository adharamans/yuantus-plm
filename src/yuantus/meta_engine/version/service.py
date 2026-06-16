"""
Version Control Service
Manages Item revisions, generations, checkouts, and history.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
import copy
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import or_, desc

from yuantus.meta_engine.version.models import (
    ItemVersion,
    VersionHistory,
    ItemIteration,
    RevisionScheme,
)
from yuantus.meta_engine.version.checkout_context import (
    apply_checkout_context,
    checkout_context_conflicts,
    clear_checkout_context,
    normalize_checkout_context,
)
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.effectivity import Effectivity
from yuantus.meta_engine.version.file_service import VersionFileService, VersionFileError


class VersionError(Exception):
    pass


class VersionService:
    def __init__(self, session: Session):
        self.session = session
        self.file_version_service = VersionFileService(
            session
        )  # Initialize VersionFileService

    def get_version(self, version_id: str) -> ItemVersion:
        """Get an ItemVersion by ID."""
        version = self.session.query(ItemVersion).get(version_id)
        if not version:
            raise VersionError(f"Version {version_id} not found")
        return version

    def create_initial_version(self, item: Item, user_id: int) -> ItemVersion:
        """
        Creates the first version (Generation 1, Revision A) for a new Item.
        """
        if not item.is_versionable:
            return None

        # Check if version exists
        exists = self.session.query(ItemVersion).filter_by(item_id=item.id).first()
        if exists:
            # Already initialized
            return exists

        ver_id = str(uuid.uuid4())
        version = ItemVersion(
            id=ver_id,
            item_id=item.id,
            generation=1,
            revision="A",
            version_label="1.A",
            state="Draft",
            is_current=True,
            is_released=False,
            # Snapshot current properties
            properties=copy.deepcopy(item.properties) if item.properties else None,
            created_by_id=user_id,
            created_at=datetime.utcnow(),
        )
        self.session.add(version)
        self.session.flush()

        # Update Item
        item.current_version_id = ver_id

        self._log_history(version, "create", user_id, "Initial version created")
        return version

    def checkout(
        self,
        item_id: str,
        user_id: int,
        comment: str = None,
        version_id: str = None,
        *,
        client_host: Optional[str] = None,
        client_workspace_path: Optional[str] = None,
        client_info: Optional[Dict[str, Any]] = None,
    ) -> ItemVersion:
        """
        Locks the current version (or specific version) for editing by the user.
        If version_id is provided, it must be the ID of a valid version.
        """
        version = None
        if version_id:
            version = self.session.query(ItemVersion).get(version_id)
            if not version or version.item_id != item_id:
                raise VersionError(f"Version {version_id} not found for item {item_id}")
        else:
            item = self.session.query(Item).filter_by(id=item_id).one()
            if not item.current_version_id:
                raise VersionError("Item has no current version")
            version = (
                self.session.query(ItemVersion)
                .filter_by(id=item.current_version_id)
                .one()
            )

        if version.is_released:
            raise VersionError(
                "Cannot checkout a Released version. Create a new revision instead."
            )

        context = normalize_checkout_context(
            client_host=client_host,
            workspace_path=client_workspace_path,
            client_info=client_info,
        )

        if version.checked_out_by_id:
            if version.checked_out_by_id == user_id:
                if checkout_context_conflicts(version, context):
                    raise VersionError(
                        "Version is already checked out by this user "
                        "from a different workstation"
                    )
                return version  # Already checked out by this user
            raise VersionError(
                f"Version is already checked out by user {version.checked_out_by_id}"
            )

        blocking_file_locks = self.file_version_service.get_blocking_file_locks(
            version.id,
            user_id=user_id,
        )
        if blocking_file_locks:
            owners = sorted(
                {
                    str(vf.checked_out_by_id)
                    for vf in blocking_file_locks
                    if vf.checked_out_by_id is not None
                }
            )
            raise VersionError(
                "Version has file-level locks held by another user"
                + (f" ({', '.join(owners)})" if owners else "")
            )

        version.checked_out_by_id = user_id
        version.checked_out_at = datetime.utcnow()
        apply_checkout_context(version, context)
        self.session.add(version)

        self._log_history(
            version,
            "checkout",
            user_id,
            comment,
            changes={"lock_context": context},
        )
        return version

    def checkin(
        self,
        item_id: str,
        user_id: int,
        properties: Dict[str, Any] = None,
        comment: str = None,
        version_id: str = None,
    ) -> ItemVersion:
        """
        Unlocks the version and saves changes.
        """
        item = self.session.query(Item).filter_by(id=item_id).one()
        version = None
        if version_id:
            version = self.session.query(ItemVersion).get(version_id)
            if not version or version.item_id != item_id:
                raise VersionError(f"Version {version_id} not found for item {item_id}")
            # Ensure it is actually checked out
            if version.checked_out_by_id != user_id:
                raise VersionError("Version is not checked out by you")
        else:
            version = (
                self.session.query(ItemVersion)
                .filter_by(id=item.current_version_id)
                .one()
            )

            if version.checked_out_by_id != user_id:
                raise VersionError("Version is not checked out by you")

        # Update properties if provided
        if properties:
            # Important: JSON columns are not mutable-tracked by default; ensure we
            # assign a fresh object so SQLAlchemy will persist the change.
            current_props = copy.deepcopy(version.properties or {})
            current_props.update(properties)
            version.properties = current_props

            # Only update Item Master if we are checking in the CURRENT version
            # If checking in a branch, do NOT update item master properties yet
            if item.current_version_id == version.id:
                item_props = copy.deepcopy(item.properties or {})
                item_props.update(properties)
                item.properties = item_props

        # Sync item file attachments to this version
        props = version.properties or {}
        extra_files = []
        native_file = props.get("native_file")
        if native_file:
            extra_files.append(
                {"file_id": native_file, "file_role": "native_cad", "is_primary": True}
            )
        viewable_file = props.get("viewable_file")
        if viewable_file:
            extra_files.append(
                {"file_id": viewable_file, "file_role": "geometry"}
            )

        primary_file_id = (
            props.get("primary_file_id")
            or props.get("native_file")
            or version.primary_file_id
        )

        include_item_files = True
        remove_missing = True
        if item.current_version_id != version.id:
            include_item_files = False
            remove_missing = False

        try:
            self.file_version_service.sync_item_files_to_version(
                item_id=item_id,
                version_id=version.id,
                user_id=user_id,
                include_item_files=include_item_files,
                extra_files=extra_files,
                primary_file_id=primary_file_id,
                remove_missing=remove_missing,
            )
        except VersionFileError as e:
            raise VersionError(str(e))

        self.file_version_service.release_all_file_locks(version.id)
        version.checked_out_by_id = None
        version.checked_out_at = None
        clear_checkout_context(version)

        self._log_history(version, "checkin", user_id, comment, changes=properties)
        return version

    # ... (revise, new_generation, release remain same)

    def _assert_no_foreign_file_locks(
        self,
        version_id: str,
        user_id: int,
        *,
        label: str,
    ) -> None:
        blocking_locks = self.file_version_service.get_blocking_file_locks(
            version_id,
            user_id=user_id,
        )
        if not blocking_locks:
            return
        owners = sorted(
            {
                str(vf.checked_out_by_id)
                for vf in blocking_locks
                if vf.checked_out_by_id is not None
            }
        )
        raise VersionError(
            f"{label} has file-level locks held by another user"
            + (f" ({', '.join(owners)})" if owners else "")
        )

    def _assert_source_version_checkout_available(
        self,
        version: ItemVersion,
        user_id: int,
        *,
        label: str = "Source version",
    ) -> None:
        if version.checked_out_by_id and version.checked_out_by_id != user_id:
            raise VersionError(
                f"{label} {version.id} is checked out by another user"
            )

    def merge_branch(
        self, item_id: str, source_version_id: str, target_version_id: str, user_id: int
    ) -> ItemVersion:
        """
        Merges changes from a source version (branch) into a target version (mainline).
        This typically involves:
        1. Taking the properties from source.
        2. Applying them to the target.
        3. Ensuring target is checked out or Creating a new Revision on target.

        Implementation: Creates a NEW revision of the target line containing source properties.
        """
        source_ver = self.session.query(ItemVersion).get(source_version_id)
        target_ver = self.session.query(ItemVersion).get(target_version_id)

        if not source_ver or not target_ver:
            raise VersionError("Source or Target version not found")

        if source_ver.item_id != item_id or target_ver.item_id != item_id:
            raise VersionError("Versions must belong to the same item")

        self._assert_source_version_checkout_available(source_ver, user_id)

        self._assert_no_foreign_file_locks(
            source_ver.id,
            user_id,
            label="Source version",
        )

        if target_ver.is_released:
            raise VersionError(f"Target version {target_ver.id} is released and locked")
        if target_ver.checked_out_by_id != user_id:
            raise VersionError(f"Target version {target_ver.id} is not checked out by you")

        self._assert_no_foreign_file_locks(
            target_ver.id,
            user_id,
            label="Target version",
        )

        # Logic: Revise Target -> New Revision, Apply Source Props
        # Reuse revise logic logic but with property override

        # Calculate next revision for target
        next_rev = self._next_revision(target_ver.revision)

        new_ver_id = str(uuid.uuid4())

        # Merge properties: Target + Source (Source overwrites Target)
        merged_props = copy.deepcopy(target_ver.properties or {})
        if source_ver.properties:
            merged_props.update(copy.deepcopy(source_ver.properties))

        new_ver = ItemVersion(
            id=new_ver_id,
            item_id=item_id,
            generation=target_ver.generation,
            revision=next_rev,
            version_label=f"{target_ver.generation}.{next_rev}",
            state="Draft",
            is_current=target_ver.is_current,  # Inherit current status (if target was current, new one becomes current)
            is_released=False,
            predecessor_id=target_ver.id,
            # B1 (D5): inherit the target's branch -- do NOT let it default to "main".
            # Otherwise a branch-target merge would land as a mainline open draft and
            # collide with the D4b (item_id, branch_name) open-current invariant.
            branch_name=target_ver.branch_name,
            properties=copy.deepcopy(merged_props) if merged_props else None,
            created_by_id=user_id,
            created_at=datetime.utcnow(),
        )

        if target_ver.is_current:
            target_ver.is_current = False
            # Update item master if target was current
            item = self.session.query(Item).get(item_id)
            item.current_version_id = new_ver_id
            # Also sync item properties
            item.properties = copy.deepcopy(merged_props) if merged_props else None

        self.session.add(new_ver)
        self.session.add(target_ver)

        self._log_history(
            new_ver,
            "merge",
            user_id,
            f"Merged branch {source_ver.branch_name or source_ver.version_label} into {target_ver.version_label}",
        )
        return new_ver

    def revise(self, item_id: str, user_id: int, comment: str = None) -> ItemVersion:
        """
        Creates a new Revision (A -> B) from the current version.
        Typical workflow: Released (A) -> Revise -> Draft (B).
        """
        item = self.session.query(Item).filter_by(id=item_id).one()
        current_ver = (
            self.session.query(ItemVersion).filter_by(id=item.current_version_id).one()
        )
        # B1 (D4): only a released version may spawn a revision. A line may hold at
        # most one open draft (enforced race-proof by the DB partial-unique on
        # is_current + not is_released); revising an in-work draft would fork the
        # line, so refuse early with a friendly error. Iterate a draft in place via
        # checkout/checkin, not a new revision. (merge_branch() is a separate branch
        # workflow and is intentionally NOT subject to this guard -- see D5.)
        if not current_ver.is_released:
            raise VersionError(
                "Cannot revise an unreleased (in-work) version; release or discard "
                "the open draft first"
            )
        self._assert_source_version_checkout_available(current_ver, user_id)

        # Calculate new revision
        # Simple logic: A -> B -> C ... Z -> AA
        next_rev = self._next_revision(current_ver.revision)

        new_ver_id = str(uuid.uuid4())
        new_ver = ItemVersion(
            id=new_ver_id,
            item_id=item.id,
            generation=current_ver.generation,
            revision=next_rev,
            version_label=f"{current_ver.generation}.{next_rev}",
            state="Draft",
            is_current=True,
            is_released=False,
            predecessor_id=current_ver.id,
            properties=copy.deepcopy(current_ver.properties)
            if current_ver.properties
            else None,  # Snapshot props
            created_by_id=user_id,
            created_at=datetime.utcnow(),
        )

        # Archive old version
        current_ver.is_current = False

        self.session.add(new_ver)
        self.session.add(current_ver)
        self.session.flush()

        item.current_version_id = new_ver_id

        self._log_history(
            current_ver, "revise", user_id, f"Revised to {new_ver.version_label}"
        )
        # Copy file associations to the new version
        try:
            self.file_version_service.copy_files_to_version(
                current_ver.id,
                new_ver.id,
                user_id=user_id,
            )
        except VersionFileError as e:
            raise VersionError(str(e))

        return new_ver

    def new_generation(
        self, item_id: str, user_id: int, comment: str = None
    ) -> ItemVersion:
        """
        Creates a new Generation (1 -> 2).
        Resets revision to A.
        """
        item = self.session.query(Item).filter_by(id=item_id).one()
        current_ver = (
            self.session.query(ItemVersion).filter_by(id=item.current_version_id).one()
        )
        # B1 (D4): only a released version may spawn a revision. A line may hold at
        # most one open draft (enforced race-proof by the DB partial-unique on
        # is_current + not is_released); revising an in-work draft would fork the
        # line, so refuse early with a friendly error. Iterate a draft in place via
        # checkout/checkin, not a new revision. (merge_branch() is a separate branch
        # workflow and is intentionally NOT subject to this guard -- see D5.)
        if not current_ver.is_released:
            raise VersionError(
                "Cannot revise an unreleased (in-work) version; release or discard "
                "the open draft first"
            )
        self._assert_source_version_checkout_available(current_ver, user_id)

        next_gen = current_ver.generation + 1
        next_rev = "A"

        new_ver_id = str(uuid.uuid4())
        new_ver = ItemVersion(
            id=new_ver_id,
            item_id=item.id,
            generation=next_gen,
            revision=next_rev,
            version_label=f"{next_gen}.{next_rev}",
            state="Draft",
            is_current=True,
            is_released=False,
            predecessor_id=current_ver.id,
            properties=copy.deepcopy(current_ver.properties)
            if current_ver.properties
            else None,  # Snapshot props
            created_by_id=user_id,
            created_at=datetime.utcnow(),
        )

        current_ver.is_current = False

        self.session.add(new_ver)
        self.session.add(current_ver)
        self.session.flush()

        item.current_version_id = new_ver_id

        # Copy file associations to the new generation
        try:
            self.file_version_service.copy_files_to_version(
                current_ver.id,
                new_ver.id,
                user_id=user_id,
            )
        except VersionFileError as e:
            raise VersionError(str(e))

        return new_ver

    def release(self, item_id: str, user_id: int) -> ItemVersion:
        """
        Marks current version as Released.
        Usually called by Lifecycle Service when Item enters Released state.
        """
        item = self.session.query(Item).filter_by(id=item_id).one()
        current_ver = (
            self.session.query(ItemVersion).filter_by(id=item.current_version_id).one()
        )

        if current_ver.is_released:
            return current_ver

        blocking_file_locks = self.file_version_service.get_blocking_file_locks(
            current_ver.id,
            user_id=user_id,
        )
        if blocking_file_locks:
            owners = sorted(
                {
                    str(vf.checked_out_by_id)
                    for vf in blocking_file_locks
                    if vf.checked_out_by_id is not None
                }
            )
            raise VersionError(
                "Version has file-level locks held by another user"
                + (f" ({', '.join(owners)})" if owners else "")
            )

        current_ver.is_released = True
        current_ver.state = "Released"
        current_ver.checked_out_by_id = None
        current_ver.checked_out_at = None
        clear_checkout_context(current_ver)
        # P1A (ECM publish hook-point hardening): stamp release provenance on the version
        # being released. release() is the canonical runtime is_released writer, so this is
        # the single place released_at/released_by_id are set. Only the just-released version
        # is stamped -- the superseded predecessor below keeps its own original provenance.
        current_ver.released_at = datetime.utcnow()
        current_ver.released_by_id = user_id

        # B1 (D2): supersede the immediate prior released version on this line. When
        # vN+1 is released, vN (predecessor) goes Released -> Superseded. It keeps
        # is_released=True (it WAS released, per Q-A); is_superseded/is_current then
        # distinguish active vs historical released. Only the immediate predecessor
        # that is still an active released version is touched (older ancestors were
        # superseded inductively when their successor released); the
        # `not is_superseded` guard stops the seeder's Suspend->Release re-cycle from
        # re-superseding an already-superseded version. release() is the SOLE runtime
        # is_released setter (promote- and ECO-originated releases both route here).
        if current_ver.predecessor_id:
            predecessor = self.session.get(ItemVersion, current_ver.predecessor_id)
            if (
                predecessor is not None
                and predecessor.is_released
                and not predecessor.is_superseded
            ):
                predecessor.is_superseded = True
                predecessor.state = "Superseded"
                self.session.add(predecessor)
                self._log_history(
                    predecessor,
                    "supersede",
                    user_id,
                    f"Superseded by {current_ver.version_label}",
                )

        self.file_version_service.release_all_file_locks(current_ver.id)

        # Lock item properties if needed? Item properties are usually master, maybe synced.

        self.session.add(current_ver)
        self._log_history(current_ver, "release", user_id, "Version Released")
        return current_ver

    def is_under_modification(self, item_id: str) -> bool:
        """B1 (D3): derived "this line is being revised" signal -- read-time only,
        no stored state. True iff the current version is an open (unreleased) draft
        AND the line has a prior released version (i.e. a released version is in the
        middle of being revised). A brand-new item with only a never-released Draft
        is NOT under modification."""
        item = self.session.get(Item, item_id)
        if item is None or not item.current_version_id:
            return False
        current = self.session.get(ItemVersion, item.current_version_id)
        if current is None or current.is_released:
            return False
        return (
            self.session.query(ItemVersion.id)
            .filter(
                ItemVersion.item_id == item_id,
                ItemVersion.is_released.is_(True),
            )
            .first()
            is not None
        )

    @staticmethod
    def _lifecycle_status(version: ItemVersion, *, is_under_modification: bool) -> str:
        """Single-value read-surface status for B1 version-axis semantics."""
        if version.is_released and version.is_superseded:
            return "historical_released"
        if version.is_released and not version.is_superseded:
            return "active_released"
        if (
            not version.is_released
            and version.is_current
            and is_under_modification
        ):
            return "in_work"
        return "draft"

    @staticmethod
    def _dt(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    def list_versions(self, item_id: str) -> Dict[str, Any]:
        """List ItemVersion rows with B1 active/historical/in-work status signals."""
        item = self.session.get(Item, item_id)
        if item is None:
            raise VersionError(f"Item {item_id} not found")

        is_under_modification = self.is_under_modification(item_id)
        versions = (
            self.session.query(ItemVersion)
            .filter(ItemVersion.item_id == item_id)
            .order_by(
                ItemVersion.generation.asc(),
                ItemVersion.created_at.asc(),
                ItemVersion.id.asc(),
            )
            .all()
        )
        return {
            "item_id": item_id,
            "is_under_modification": is_under_modification,
            "versions": [
                {
                    "version_id": version.id,
                    "version_label": version.version_label,
                    "generation": version.generation,
                    "revision": version.revision,
                    "branch_name": version.branch_name,
                    "state": version.state,
                    "is_current": bool(version.is_current),
                    "is_released": bool(version.is_released),
                    "is_superseded": bool(version.is_superseded),
                    "lifecycle_status": self._lifecycle_status(
                        version,
                        is_under_modification=is_under_modification,
                    ),
                    "predecessor_id": version.predecessor_id,
                    "created_at": self._dt(version.created_at),
                    "released_at": self._dt(version.released_at),
                }
                for version in versions
            ],
        }

    def get_history(self, item_id: str) -> List[VersionHistory]:
        """
        Get flat history of all versions of an item.
        """
        # Join ItemVersion to filter by item_id
        return (
            self.session.query(VersionHistory)
            .join(ItemVersion, VersionHistory.version_id == ItemVersion.id)
            .filter(ItemVersion.item_id == item_id)
            .order_by(desc(VersionHistory.created_at))
            .all()
        )

    def _next_revision(self, current_rev: str, scheme: str = "letter") -> str:
        """
        Calculates next revision string with full sequence support.

        Schemes:
        - letter: A→B...Z→AA→AB...AZ→BA...ZZ→AAA (Excel-style columns)
        - number: 1→2→3...
        - hybrid: A1→A2→A3...A99→B1 (letter+number)

        Examples (letter scheme):
        - A → B
        - Z → AA
        - AA → AB
        - AZ → BA
        - ZZ → AAA
        """
        if not current_rev:
            return "A" if scheme == "letter" else "1"

        if scheme == "number":
            return str(int(current_rev) + 1)

        if scheme == "hybrid":
            return self._next_hybrid_revision(current_rev)

        if scheme == "semantic":
            return self._next_semantic_revision(current_rev)

        # Letter scheme (Excel-style column naming)
        return self._increment_letter_revision(current_rev)

    def _next_semantic_revision(self, rev: str) -> str:
        """
        Increment Semantic Version (Major.Minor.Patch).
        Default behavior: Increment Patch.
        1.0.0 -> 1.0.1
        0.1 -> 0.2
        """
        if not rev:
            return "0.0.1"

        parts = rev.split(".")
        try:
            # Increment last part
            parts[-1] = str(int(parts[-1]) + 1)
            return ".".join(parts)
        except ValueError:
            # Fallback if not integer
            return rev + ".1"

    def _increment_letter_revision(self, rev: str) -> str:
        """
        Increment letter-based revision like Excel columns.
        A→Z, then AA→AZ, BA→BZ, ..., ZZ→AAA
        """
        if not rev:
            return "A"

        # Convert to list for manipulation
        chars = list(rev.upper())

        # Work backwards, incrementing like base-26
        i = len(chars) - 1
        while i >= 0:
            if chars[i] == "Z":
                chars[i] = "A"
                i -= 1
            else:
                chars[i] = chr(ord(chars[i]) + 1)
                return "".join(chars)

        # All chars were Z, need to add new char
        return "A" + "".join(chars)

    def _next_hybrid_revision(self, rev: str) -> str:
        """
        Hybrid revision: Letter prefix + Number suffix.
        A1→A2...A99→B1, Z99→AA1
        """
        import re

        match = re.match(r"^([A-Z]+)(\d+)$", rev.upper())

        if not match:
            return "A1"

        letter_part = match.group(1)
        num_part = int(match.group(2))

        # Increment number up to 99
        if num_part < 99:
            return f"{letter_part}{num_part + 1}"

        # Roll over to next letter
        next_letter = self._increment_letter_revision(letter_part)
        return f"{next_letter}1"

    def parse_revision(self, rev: str) -> dict:
        """
        Parse a revision string into components.
        Returns: {scheme, letter_part, number_part, value}
        """
        import re

        if not rev:
            return {"scheme": "unknown", "value": 0}

        # Pure number
        if rev.isdigit():
            return {"scheme": "number", "value": int(rev)}

        # Pure letter
        if rev.isalpha():
            value = 0
            for c in rev.upper():
                value = value * 26 + (ord(c) - ord("A") + 1)
            return {"scheme": "letter", "value": value}

        # Hybrid
        match = re.match(r"^([A-Z]+)(\d+)$", rev.upper())
        if match:
            letter_val = 0
            for c in match.group(1):
                letter_val = letter_val * 26 + (ord(c) - ord("A") + 1)
            num_val = int(match.group(2))
            return {
                "scheme": "hybrid",
                "letter_part": match.group(1),
                "number_part": num_val,
                "value": letter_val * 100 + num_val,
            }

        return {"scheme": "unknown", "value": 0}

    def compare_revisions(self, rev_a: str, rev_b: str) -> int:
        """
        Compare two revisions.
        Returns: -1 if a < b, 0 if equal, 1 if a > b
        """
        parsed_a = self.parse_revision(rev_a)
        parsed_b = self.parse_revision(rev_b)

        val_a = parsed_a.get("value", 0)
        val_b = parsed_b.get("value", 0)

        if val_a < val_b:
            return -1
        elif val_a > val_b:
            return 1
        return 0

    def _log_history(
        self,
        version: ItemVersion,
        action: str,
        user_id: int,
        comment: str,
        changes: Dict = None,
    ):
        hist = VersionHistory(
            id=str(uuid.uuid4()),
            version_id=version.id,
            action=action,
            user_id=user_id,
            comment=comment,
            changes=changes,
            created_at=datetime.utcnow(),
        )
        self.session.add(hist)

    def add_date_effectivity(
        self, version_id: str, start_date: datetime, end_date: datetime = None
    ) -> Effectivity:
        """
        Adds a date-based effectivity rule to a version.
        """
        eff = Effectivity(
            id=str(uuid.uuid4()),
            version_id=version_id,
            effectivity_type="Date",
            start_date=start_date,
            end_date=end_date,
        )
        self.session.add(eff)
        return eff

    def find_effective_version(
        self, item_id: str, target_date: datetime
    ) -> Optional[ItemVersion]:
        """
        Finds the version of an item that is effective on the given date.
        """
        query = (
            self.session.query(ItemVersion)
            .join(Effectivity, ItemVersion.id == Effectivity.version_id)
            .filter(ItemVersion.item_id == item_id)
            .filter(Effectivity.effectivity_type == "Date")
            .filter(Effectivity.start_date <= target_date)
            .filter(
                or_(Effectivity.end_date == None, Effectivity.end_date >= target_date)
            )
            .order_by(desc(ItemVersion.created_at))
        )

        return query.first()

    def create_branch(
        self, item_id: str, source_version_id: str, branch_name: str, user_id: int
    ) -> ItemVersion:
        """
        Creates a new branch from a specific version.
        """
        source_ver = self.session.query(ItemVersion).get(source_version_id)
        if not source_ver:
            raise VersionError("Source version not found")
        self._assert_source_version_checkout_available(source_ver, user_id)

        new_ver_id = str(uuid.uuid4())
        # Branch usually starts new revision sequence or appends branch name
        # Scheme: {Generation}.{Revision}-{Branch}

        new_ver = ItemVersion(
            id=new_ver_id,
            item_id=item_id,
            generation=source_ver.generation,
            revision=source_ver.revision,  # Start same, but marked with branch
            branch_name=branch_name,
            version_label=f"{source_ver.version_label}-{branch_name}",
            state="Draft",
            is_current=False,  # Branches are usually not "Current" main line immediately
            is_released=False,
            predecessor_id=source_ver.id,
            properties=copy.deepcopy(source_ver.properties) if source_ver.properties else None,
            created_by_id=user_id,
            created_at=datetime.utcnow(),
        )

        self.session.add(new_ver)
        self.session.flush()
        try:
            self.file_version_service.copy_files_to_version(
                source_ver.id,
                new_ver.id,
                user_id=user_id,
            )
        except VersionFileError as e:
            raise VersionError(str(e))
        self._log_history(
            new_ver,
            "branch",
            user_id,
            f"Branched '{branch_name}' from {source_ver.version_label}",
        )
        return new_ver

    def get_version_tree(self, item_id: str) -> List[Dict[str, Any]]:
        """
        Returns a flat list of all versions with parent pointers to reconstruct tree.
        """
        versions = self.session.query(ItemVersion).filter_by(item_id=item_id).all()
        return [
            {
                "id": v.id,
                "label": v.version_label,
                "predecessor_id": v.predecessor_id,
                "branch": v.branch_name,
                "state": v.state,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ]

    def compare_versions(self, version_id_a: str, version_id_b: str) -> Dict[str, Any]:
        """
        Compare properties of two versions.
        """
        va = self.session.query(ItemVersion).get(version_id_a)
        vb = self.session.query(ItemVersion).get(version_id_b)

        if not va or not vb:
            raise VersionError("One or more versions not found")

        props_a = va.properties or {}
        props_b = vb.properties or {}

        all_keys = set(props_a.keys()) | set(props_b.keys())
        diffs = {}

        for k in all_keys:
            val_a = props_a.get(k)
            val_b = props_b.get(k)
            if val_a != val_b:
                diffs[k] = {"a": val_a, "b": val_b}

        return {
            "version_a": va.version_label,
            "version_b": vb.version_label,
            "diffs": diffs,
        }


class IterationService:
    """
    Sprint 4 Enhancement: Lightweight iteration management.
    Iterations are work-in-progress saves within a formal version.
    """

    def __init__(self, session: Session):
        self.session = session

    def create_iteration(
        self,
        version_id: str,
        user_id: int,
        properties: Dict[str, Any] = None,
        description: str = None,
        source_type: str = "manual",
    ) -> ItemIteration:
        """
        Create a new iteration within a version.

        Args:
            version_id: Parent version ID
            user_id: User creating the iteration
            properties: Property snapshot
            description: Iteration description
            source_type: manual, auto_save, import

        Returns:
            New ItemIteration
        """
        version = self.session.query(ItemVersion).get(version_id)
        if not version:
            raise VersionError(f"Version {version_id} not found")

        # Get next iteration number
        max_iter = (
            self.session.query(ItemIteration).filter_by(version_id=version_id).count()
        )
        next_number = max_iter + 1

        # Mark previous iterations as not latest
        self.session.query(ItemIteration).filter_by(
            version_id=version_id, is_latest=True
        ).update({"is_latest": False})

        # Create new iteration
        iteration = ItemIteration(
            id=str(uuid.uuid4()),
            version_id=version_id,
            iteration_number=next_number,
            iteration_label=f"{version.version_label}.{next_number}",
            is_latest=True,
            properties=copy.deepcopy(properties)
            if properties is not None
            else copy.deepcopy(version.properties or {}),
            description=description,
            source_type=source_type,
            created_by_id=user_id,
            created_at=datetime.utcnow(),
        )
        self.session.add(iteration)
        self.session.flush()

        return iteration

    def get_iterations(self, version_id: str) -> List[ItemIteration]:
        """Get all iterations for a version, ordered by iteration number."""
        return (
            self.session.query(ItemIteration)
            .filter_by(version_id=version_id)
            .order_by(ItemIteration.iteration_number)
            .all()
        )

    def get_latest_iteration(self, version_id: str) -> Optional[ItemIteration]:
        """Get the latest iteration for a version."""
        return (
            self.session.query(ItemIteration)
            .filter_by(version_id=version_id, is_latest=True)
            .first()
        )

    def restore_iteration(self, iteration_id: str, user_id: int) -> ItemIteration:
        """
        Restore a previous iteration as the latest.
        Creates a new iteration with the old iteration's data.
        """
        old_iter = self.session.query(ItemIteration).get(iteration_id)
        if not old_iter:
            raise VersionError(f"Iteration {iteration_id} not found")

        # Create new iteration from old one
        return self.create_iteration(
            version_id=old_iter.version_id,
            user_id=user_id,
            properties=old_iter.properties,
            description=f"Restored from iteration {old_iter.iteration_label}",
            source_type="manual",
        )

    def delete_iteration(self, iteration_id: str) -> bool:
        """
        Delete an iteration (not the latest one).
        """
        iteration = self.session.query(ItemIteration).get(iteration_id)
        if not iteration:
            return False

        if iteration.is_latest:
            raise VersionError("Cannot delete the latest iteration")

        self.session.delete(iteration)
        self.session.flush()
        return True


class RevisionSchemeService:
    """
    Sprint 4 Enhancement: Manage revision numbering schemes.
    """

    def __init__(self, session: Session):
        self.session = session

    def create_scheme(
        self,
        name: str,
        scheme_type: str = "letter",
        initial_revision: str = "A",
        item_type_id: str = None,
        is_default: bool = False,
        description: str = None,
    ) -> RevisionScheme:
        """
        Create a new revision scheme.

        Args:
            name: Scheme name
            scheme_type: letter, number, or hybrid
            initial_revision: Starting revision value
            item_type_id: Optional ItemType to associate with
            is_default: Whether this is the default scheme
            description: Optional description
        """
        # If setting as default, clear existing defaults
        if is_default:
            if item_type_id:
                self.session.query(RevisionScheme).filter_by(
                    item_type_id=item_type_id, is_default=True
                ).update({"is_default": False})
            else:
                self.session.query(RevisionScheme).filter_by(
                    item_type_id=None, is_default=True
                ).update({"is_default": False})

        scheme = RevisionScheme(
            id=str(uuid.uuid4()),
            name=name,
            scheme_type=scheme_type,
            initial_revision=initial_revision,
            item_type_id=item_type_id,
            is_default=is_default,
            description=description,
            created_at=datetime.utcnow(),
        )
        self.session.add(scheme)
        self.session.flush()
        return scheme

    def get_scheme_for_item_type(self, item_type_id: str) -> RevisionScheme:
        """
        Get the revision scheme for a specific ItemType.
        Falls back to global default if no type-specific scheme exists.
        """
        # Try type-specific scheme first
        scheme = (
            self.session.query(RevisionScheme)
            .filter_by(item_type_id=item_type_id, is_default=True)
            .first()
        )

        if scheme:
            return scheme

        # Fall back to global default
        scheme = (
            self.session.query(RevisionScheme)
            .filter_by(item_type_id=None, is_default=True)
            .first()
        )

        return scheme

    def list_schemes(self) -> List[RevisionScheme]:
        """List all revision schemes."""
        return (
            self.session.query(RevisionScheme)
            .order_by(RevisionScheme.item_type_id.nullsfirst(), RevisionScheme.name)
            .all()
        )

    def get_initial_revision(self, item_type_id: str = None) -> str:
        """
        Get the initial revision for an item type.
        """
        scheme = self.get_scheme_for_item_type(item_type_id) if item_type_id else None

        if scheme:
            return scheme.initial_revision

        # Default fallback
        return "A"

    def get_scheme_type(self, item_type_id: str = None) -> str:
        """
        Get the scheme type (letter/number/hybrid) for an item type.
        """
        scheme = self.get_scheme_for_item_type(item_type_id) if item_type_id else None

        if scheme:
            return scheme.scheme_type

        # Default fallback
        return "letter"
