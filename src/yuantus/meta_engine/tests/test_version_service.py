import pytest
from unittest.mock import MagicMock
from yuantus.meta_engine.version.service import VersionService
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.meta_engine.version.service import VersionError
from yuantus.meta_engine.version.file_service import VersionFileError


class TestVersionService:
    @pytest.fixture
    def mock_session(self):
        return MagicMock()

    def test_create_initial_version(self, mock_session):
        service = VersionService(mock_session)

        # Mock Item
        item = Item(id="item-1", is_versionable=True, properties={})

        # Determine if version exists
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        ver = service.create_initial_version(item, user_id=1)

        assert ver is not None
        assert ver.generation == 1
        assert ver.revision == "A"
        assert ver.is_current is True
        assert item.current_version_id == ver.id

        mock_session.add.assert_called()

    def test_checkout(self, mock_session):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1")
        version = ItemVersion(
            id="ver-1",
            item_id="item-1",
            state="Draft",
            is_released=False,
            checked_out_by_id=None,
        )

        # Mock queries
        mock_session.query.return_value.filter_by.side_effect = [
            MagicMock(one=lambda: item),
            MagicMock(one=lambda: version),
        ]
        service.file_version_service.get_blocking_file_locks = MagicMock(return_value=[])

        checked_out = service.checkout("item-1", user_id=2, comment="Edit")

        assert checked_out.checked_out_by_id == 2
        assert checked_out.checked_out_at is not None

    def test_checkout_rejects_conflicting_file_locks(self, mock_session):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1")
        version = ItemVersion(
            id="ver-1",
            item_id="item-1",
            state="Draft",
            is_released=False,
            checked_out_by_id=None,
        )

        mock_session.query.return_value.filter_by.side_effect = [
            MagicMock(one=lambda: item),
            MagicMock(one=lambda: version),
        ]
        service.file_version_service.get_blocking_file_locks = MagicMock(
            return_value=[
                MagicMock(
                    file_id="file-1",
                    file_role="preview",
                    checked_out_by_id=9,
                )
            ]
        )

        with pytest.raises(VersionError, match="file-level locks held by another user"):
            service.checkout("item-1", user_id=2, comment="Edit")

    def test_checkin_releases_all_file_locks(self, mock_session):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1", properties={})
        version = ItemVersion(
            id="ver-1",
            item_id="item-1",
            state="Draft",
            is_released=False,
            checked_out_by_id=2,
            properties={},
        )

        mock_session.query.return_value.filter_by.side_effect = [
            MagicMock(one=lambda: item),
            MagicMock(one=lambda: version),
        ]
        service.file_version_service.sync_item_files_to_version = MagicMock()
        service.file_version_service.release_all_file_locks = MagicMock(return_value=1)

        checked_in = service.checkin("item-1", user_id=2, comment="Done")

        assert checked_in.checked_out_by_id is None
        assert checked_in.checked_out_at is None
        service.file_version_service.sync_item_files_to_version.assert_called_once_with(
            item_id="item-1",
            version_id="ver-1",
            user_id=2,
            include_item_files=True,
            extra_files=[],
            primary_file_id=None,
            remove_missing=True,
        )
        service.file_version_service.release_all_file_locks.assert_called_once_with(
            "ver-1"
        )

    def test_checkin_surfaces_sync_item_files_foreign_lock_error(self, mock_session):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1", properties={})
        version = ItemVersion(
            id="ver-1",
            item_id="item-1",
            state="Draft",
            is_released=False,
            checked_out_by_id=2,
            properties={},
        )

        mock_session.query.return_value.filter_by.side_effect = [
            MagicMock(one=lambda: item),
            MagicMock(one=lambda: version),
        ]
        service.file_version_service.sync_item_files_to_version = MagicMock(
            side_effect=VersionFileError(
                "Version has file-level locks held by another user (9)"
            )
        )

        with pytest.raises(
            VersionError,
            match="Version has file-level locks held by another user",
        ):
            service.checkin("item-1", user_id=2, comment="Done")

    def test_revise(self, mock_session):
        service = VersionService(mock_session)
        service.file_version_service.copy_files_to_version = MagicMock(return_value=[])

        item = Item(id="item-1", current_version_id="ver-1")
        current_ver = ItemVersion(
            id="ver-1",
            item_id="item-1",
            generation=1,
            revision="A",
            version_label="1.A",
            is_current=True,
            is_released=True,  # B1: revise/new_generation require a released source
        )

        # Mock queries
        item_query = MagicMock()
        item_query.filter_by.return_value.one.return_value = item

        ver_query = MagicMock()
        ver_query.filter_by.return_value.one.return_value = current_ver

        mock_session.query.side_effect = [item_query, ver_query]

        new_ver = service.revise("item-1", user_id=1)

        assert new_ver.generation == 1
        assert new_ver.revision == "B"
        assert new_ver.predecessor_id == "ver-1"
        assert current_ver.is_current is False
        assert item.current_version_id == new_ver.id
        service.file_version_service.copy_files_to_version.assert_called_once_with(
            "ver-1",
            new_ver.id,
            user_id=1,
        )

    def test_revise_rejects_foreign_source_file_locks(self, mock_session):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1")
        current_ver = ItemVersion(
            id="ver-1",
            item_id="item-1",
            generation=1,
            revision="A",
            version_label="1.A",
            is_current=True,
            is_released=True,  # B1: revise/new_generation require a released source
        )

        item_query = MagicMock()
        item_query.filter_by.return_value.one.return_value = item

        ver_query = MagicMock()
        ver_query.filter_by.return_value.one.return_value = current_ver

        mock_session.query.side_effect = [item_query, ver_query]
        service.file_version_service.copy_files_to_version = MagicMock(
            side_effect=VersionFileError(
                "Source version has file-level locks held by another user (9)"
            )
        )

        with pytest.raises(
            VersionError,
            match="Source version has file-level locks held by another user",
        ):
            service.revise("item-1", user_id=1)

    def test_revise_rejects_source_version_checked_out_by_another_user(
        self, mock_session
    ):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1")
        current_ver = ItemVersion(
            id="ver-1",
            item_id="item-1",
            generation=1,
            revision="A",
            version_label="1.A",
            is_current=True,
            is_released=True,  # B1: revise/new_generation require a released source
            checked_out_by_id=9,
        )

        item_query = MagicMock()
        item_query.filter_by.return_value.one.return_value = item

        ver_query = MagicMock()
        ver_query.filter_by.return_value.one.return_value = current_ver

        mock_session.query.side_effect = [item_query, ver_query]

        with pytest.raises(
            VersionError,
            match="Source version ver-1 is checked out by another user",
        ):
            service.revise("item-1", user_id=1)

    def test_release_releases_all_file_locks(self, mock_session):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1")
        current_ver = ItemVersion(
            id="ver-1",
            item_id="item-1",
            generation=1,
            revision="A",
            version_label="1.A",
            is_current=True,
            is_released=False,
            checked_out_by_id=7,
        )

        item_query = MagicMock()
        item_query.filter_by.return_value.one.return_value = item

        ver_query = MagicMock()
        ver_query.filter_by.return_value.one.return_value = current_ver

        mock_session.query.side_effect = [item_query, ver_query]
        service.file_version_service.get_blocking_file_locks = MagicMock(return_value=[])
        service.file_version_service.release_all_file_locks = MagicMock(return_value=2)

        released = service.release("item-1", user_id=7)

        assert released.is_released is True
        assert released.checked_out_by_id is None
        service.file_version_service.get_blocking_file_locks.assert_called_once_with(
            "ver-1",
            user_id=7,
        )
        service.file_version_service.release_all_file_locks.assert_called_once_with(
            "ver-1"
        )

    def test_release_rejects_foreign_file_locks(self, mock_session):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1")
        current_ver = ItemVersion(
            id="ver-1",
            item_id="item-1",
            generation=1,
            revision="A",
            version_label="1.A",
            is_current=True,
            is_released=False,
        )

        item_query = MagicMock()
        item_query.filter_by.return_value.one.return_value = item

        ver_query = MagicMock()
        ver_query.filter_by.return_value.one.return_value = current_ver

        mock_session.query.side_effect = [item_query, ver_query]
        service.file_version_service.get_blocking_file_locks = MagicMock(
            return_value=[
                MagicMock(
                    file_id="file-1",
                    file_role="preview",
                    checked_out_by_id=9,
                )
            ]
        )
        service.file_version_service.release_all_file_locks = MagicMock()

        with pytest.raises(
            VersionError,
            match="Version has file-level locks held by another user",
        ):
            service.release("item-1", user_id=7)

        service.file_version_service.release_all_file_locks.assert_not_called()

    def test_new_generation_rejects_foreign_source_file_locks(self, mock_session):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1")
        current_ver = ItemVersion(
            id="ver-1",
            item_id="item-1",
            generation=1,
            revision="A",
            version_label="1.A",
            is_current=True,
            is_released=True,  # B1: revise/new_generation require a released source
        )

        item_query = MagicMock()
        item_query.filter_by.return_value.one.return_value = item

        ver_query = MagicMock()
        ver_query.filter_by.return_value.one.return_value = current_ver

        mock_session.query.side_effect = [item_query, ver_query]
        service.file_version_service.copy_files_to_version = MagicMock(
            side_effect=VersionFileError(
                "Source version has file-level locks held by another user (9)"
            )
        )

        with pytest.raises(
            VersionError,
            match="Source version has file-level locks held by another user",
        ):
            service.new_generation("item-1", user_id=1)

    def test_new_generation_rejects_source_version_checked_out_by_another_user(
        self, mock_session
    ):
        service = VersionService(mock_session)

        item = Item(id="item-1", current_version_id="ver-1")
        current_ver = ItemVersion(
            id="ver-1",
            item_id="item-1",
            generation=1,
            revision="A",
            version_label="1.A",
            is_current=True,
            is_released=True,  # B1: revise/new_generation require a released source
            checked_out_by_id=9,
        )

        item_query = MagicMock()
        item_query.filter_by.return_value.one.return_value = item

        ver_query = MagicMock()
        ver_query.filter_by.return_value.one.return_value = current_ver

        mock_session.query.side_effect = [item_query, ver_query]

        with pytest.raises(
            VersionError,
            match="Source version ver-1 is checked out by another user",
        ):
            service.new_generation("item-1", user_id=1)
