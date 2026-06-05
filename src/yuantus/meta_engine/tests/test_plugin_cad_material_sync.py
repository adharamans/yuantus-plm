from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yuantus.models.base import Base
from yuantus.meta_engine.lifecycle.models import (
    LifecycleMap,
    LifecycleState,
    LifecycleTransition,
    StateIdentityPermission,
)
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType, Property
from yuantus.meta_engine.models.plugin_config import PluginConfig
from yuantus.meta_engine.permission.models import Access, Permission
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.meta_engine.workflow.models import WorkflowMap
from yuantus.security.rbac.models import (
    RBACPermission,
    RBACResource,
    RBACRole,
    RBACUser,
    rbac_user_permissions,
    rbac_user_roles,
    role_permissions,
)


def _load_plugin_module():
    root = Path(__file__).resolve().parents[4]
    plugin_path = root / "plugins" / "yuantus-cad-material-sync" / "main.py"
    spec = importlib.util.spec_from_file_location("cad_material_sync_plugin", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _material_sync_db_tables():
    return [
        RBACResource.__table__,
        RBACPermission.__table__,
        RBACRole.__table__,
        RBACUser.__table__,
        rbac_user_roles,
        role_permissions,
        rbac_user_permissions,
        Permission.__table__,
        Access.__table__,
        WorkflowMap.__table__,
        LifecycleMap.__table__,
        LifecycleState.__table__,
        LifecycleTransition.__table__,
        StateIdentityPermission.__table__,
        ItemType.__table__,
        Property.__table__,
        PluginConfig.__table__,
        ItemVersion.__table__,
        Item.__table__,
    ]


@pytest.fixture()
def material_sync_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=_material_sync_db_tables())
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    db.add(
        ItemType(
            id="Part",
            label="Part",
            is_relationship=False,
            is_versionable=False,
            version_control_enabled=False,
        )
    )
    db.commit()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine, tables=list(reversed(_material_sync_db_tables())))


def _db_client(module, db):
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: db
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(
        id="1",
        roles=["admin"],
        is_superuser=True,
    )
    return TestClient(app)


def _add_part(db, item_id: str, properties: dict, *, state: str = "Released") -> Item:
    item = Item(
        id=item_id,
        item_type_id="Part",
        config_id=str(uuid4()),
        generation=1,
        state=state,
        properties=properties,
    )
    db.add(item)
    db.commit()
    return item


def test_default_profiles_cover_material_categories():
    module = _load_plugin_module()

    profiles = module.load_profiles(config={})

    assert set(profiles) >= {"sheet", "tube", "bar", "forging"}
    assert profiles["sheet"]["compose"]["target"] == "specification"
    assert profiles["tube"]["fields"][2]["name"] == "outer_diameter"


def test_profile_governance_marks_specification_as_derived_cache():
    module = _load_plugin_module()

    profile = module.load_profiles(config={})["sheet"]
    governance = profile["governance"]

    assert governance["derived_fields"]["specification"] == {
        "role": "derived_cache",
        "cache": True,
        "source_of_truth": False,
        "sources": ["length", "width", "thickness"],
        "template": "{length}*{width}*{thickness}",
        "recompute_policy": "recompute_from_source_fields",
        "mismatch_warning": "derived_field_mismatch",
    }
    source_by_name = {field["name"]: field for field in governance["source_fields"]}
    assert source_by_name["length"]["role"] == "source_of_truth"
    assert source_by_name["length"]["unit"] == "mm"
    assert source_by_name["length"]["cad_keys"] == ["长"]
    assert source_by_name["thickness"]["cad_keys"] == ["厚"]
    assert governance["dynamic_property_templates"]["property_name"] == (
        "{profile_id}_{field_name}"
    )


def test_sheet_compose_builds_specification_and_cad_fields():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["sheet"]

    properties, composed, errors, warnings = module.compose_profile(
        profile,
        {
            "material": "Q235",
            "length": "1200",
            "width": 600,
            "thickness": 8,
        },
    )

    assert errors == []
    assert warnings == []
    assert properties["material_category"] == "sheet"
    assert properties["specification"] == "1200*600*8"
    assert composed == {"specification": "1200*600*8"}
    assert module.cad_field_package(profile, properties)["规格"] == "1200*600*8"


def test_compose_warns_when_incoming_derived_specification_is_recomputed():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["sheet"]

    properties, composed, errors, warnings = module.compose_profile(
        profile,
        {
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
            "specification": "旧规格",
        },
    )

    assert errors == []
    assert properties["specification"] == "1200*600*12"
    assert composed == {"specification": "1200*600*12"}
    assert warnings == [
        "derived_field_mismatch:specification: input value "
        "'旧规格' was replaced by '1200*600*12'"
    ]


def test_tube_and_bar_profiles_render_phi_specs():
    module = _load_plugin_module()
    profiles = module.load_profiles(config={})

    tube_properties, _, tube_errors, _ = module.compose_profile(
        profiles["tube"],
        {
            "material": "20#",
            "outer_diameter": 42,
            "wall_thickness": 3,
            "length": 6000,
        },
    )
    bar_properties, _, bar_errors, _ = module.compose_profile(
        profiles["bar"],
        {"material": "45#", "diameter": 30, "length": 1000},
    )

    assert tube_errors == []
    assert tube_properties["specification"] == "Φ42*3*6000"
    assert bar_errors == []
    assert bar_properties["specification"] == "Φ30*1000"


def test_validate_reports_required_and_type_errors():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["sheet"]

    properties, _composed, errors, _warnings = module.compose_profile(
        profile,
        {
            "length": "bad-number",
            "width": 600,
            "thickness": 8,
        },
    )

    assert properties["width"] == 600
    assert {error["field"] for error in errors} == {"material", "length"}
    assert {error["code"] for error in errors} == {"required", "invalid_number"}


def test_cad_fields_to_properties_reverse_maps_chinese_fields():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["sheet"]

    properties = module.cad_fields_to_properties(
        profile,
        {
            "材料": "Q235",
            "长": "1200",
            "宽": "600",
            "厚": "8",
            "规格": "1200*600*8",
        },
    )

    assert properties == {
        "material": "Q235",
        "length": "1200",
        "width": "600",
        "thickness": "8",
        "specification": "1200*600*8",
    }


def test_cad_field_aliases_support_multiple_cad_templates():
    module = _load_plugin_module()
    profile = module.load_profiles(
        config={
            "profiles": {
                "sheet": {
                    "cad_mapping": {
                        "material": {
                            "default": "材料",
                            "aliases": ["材质", "Material"],
                            "solidworks": "SW-Material@Part",
                        },
                        "specification": ["规格", "物料规格", "SW-Specification@Part"],
                    }
                }
            }
        }
    )["sheet"]
    for field in profile["fields"]:
        if field.get("name") == "length":
            field["cad_aliases"] = ["长度", "LENGTH"]
        if field.get("name") == "width":
            field["cad_keys"] = ["宽", "WIDTH"]
        if field.get("name") == "thickness":
            field["cad_key_by_connector"] = {
                "autocad": "厚",
                "solidworks": "THICKNESS",
            }

    properties = module.cad_fields_to_properties(
        profile,
        {
            "SW-Material@Part": "Q235B",
            "LENGTH": "1200",
            "WIDTH": "600",
            "THICKNESS": "12",
            "SW-Specification@Part": "legacy",
        },
    )

    assert properties == {
        "material": "Q235B",
        "length": "1200",
        "width": "600",
        "thickness": "12",
        "specification": "legacy",
    }

    composed, _composed, errors, _warnings = module.compose_profile(profile, properties)
    cad_fields = module.cad_field_package(profile, composed)

    assert errors == []
    assert composed["specification"] == "1200*600*12"
    assert cad_fields["材料"] == "Q235B"
    assert cad_fields["规格"] == "1200*600*12"
    assert cad_fields["长"] == 1200
    assert cad_fields["宽"] == 600
    assert cad_fields["厚"] == 12
    assert "SW-Material@Part" not in cad_fields


def test_cad_field_package_can_select_target_cad_system_keys():
    module = _load_plugin_module()
    profile = module.load_profiles(
        config={
            "profiles": {
                "sheet": {
                    "cad_mapping": {
                        "material": {
                            "default": "材料",
                            "solidworks": "SW-Material@Part",
                        },
                        "specification": {
                            "default": "规格",
                            "solidworks": "SW-Specification@Part",
                        },
                    }
                }
            }
        }
    )["sheet"]
    for field in profile["fields"]:
        if field.get("name") == "length":
            field["cad_key_by_connector"] = {"solidworks": "SW-Length@Part"}
        if field.get("name") == "width":
            field["cad_key_by_connector"] = {"solidworks": "SW-Width@Part"}
        if field.get("name") == "thickness":
            field["cad_key_by_connector"] = {"solidworks": "SW-Thickness@Part"}

    properties, _composed, errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
        },
    )
    default_fields = module.cad_field_package(profile, properties)
    solidworks_fields = module.cad_field_package(
        profile,
        properties,
        cad_system="solidworks",
    )

    assert errors == []
    assert default_fields["材料"] == "Q235B"
    assert default_fields["规格"] == "1200*600*12"
    assert "SW-Material@Part" not in default_fields
    assert solidworks_fields == {
        "物料类别": "sheet",
        "SW-Material@Part": "Q235B",
        "SW-Length@Part": 1200,
        "SW-Width@Part": 600,
        "SW-Thickness@Part": 12,
        "SW-Specification@Part": "1200*600*12",
    }


def test_profile_config_override_merges_default_mapping_and_adds_profile():
    module = _load_plugin_module()

    profiles = module.load_profiles(
        config={
            "profiles": {
                "sheet": {
                    "compose": {"template": "T{thickness}-{length}x{width}"},
                    "cad_mapping": {"specification": "物料规格"},
                },
                "angle": {
                    "profile_id": "angle",
                    "label": "角钢",
                    "item_type": "Part",
                    "selector": {"material_category": "angle"},
                    "fields": [
                        {
                            "name": "material",
                            "label": "材料",
                            "type": "string",
                            "required": True,
                        },
                        {
                            "name": "leg_a",
                            "label": "边宽A",
                            "type": "number",
                            "required": True,
                        },
                    ],
                    "compose": {"target": "specification", "template": "L{leg_a}"},
                    "cad_mapping": {"material": "材料", "specification": "规格"},
                },
            }
        }
    )

    assert profiles["sheet"]["compose"]["template"] == "T{thickness}-{length}x{width}"
    assert profiles["sheet"]["cad_mapping"]["item_number"] == "图号"
    assert profiles["sheet"]["cad_mapping"]["specification"] == "物料规格"
    assert profiles["angle"]["label"] == "角钢"


def test_profile_version_selects_explicit_active_version():
    module = _load_plugin_module()

    profiles = module.load_profiles(
        config={
            "profiles": {
                "sheet": {
                    "active_version": "v2",
                    "versions": {
                        "v1": {
                            "compose": {"template": "{length}*{width}*{thickness}"},
                        },
                        "v2": {
                            "compose": {"template": "PL{thickness}-{length}x{width}"},
                            "cad_mapping": {"specification": "物料规格"},
                        },
                    },
                }
            }
        }
    )
    profile = profiles["sheet"]

    properties, _composed, errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
        },
    )

    assert profile["profile_version"] == "v2"
    assert profile["available_versions"] == ["v1", "v2"]
    assert errors == []
    assert properties["specification"] == "PL12-1200x600"
    assert module.cad_field_package(profile, properties)["物料规格"] == "PL12-1200x600"


def test_profile_version_rollout_uses_context_and_default_fallback():
    from yuantus.context import tenant_id_var

    module = _load_plugin_module()
    config = {
        "profiles": {
            "sheet": {
                "default_version": "v1",
                "versions": {
                    "v1": {
                        "compose": {"template": "STD-{length}*{width}*{thickness}"},
                    },
                    "v2": {
                        "compose": {"template": "PILOT-{thickness}-{length}x{width}"},
                        "rollout": {"tenant_ids": ["tenant-pilot"]},
                    },
                },
            }
        }
    }

    default_profile = module.load_profiles(config=config)["sheet"]

    assert default_profile["profile_version"] == "v1"
    default_properties, _composed, default_errors, _warnings = module.compose_profile(
        default_profile,
        {
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
        },
    )
    assert default_errors == []
    assert default_properties["specification"] == "STD-1200*600*12"

    tenant_token = tenant_id_var.set("tenant-pilot")
    try:
        pilot_profile = module.load_profiles(config=config)["sheet"]
    finally:
        tenant_id_var.reset(tenant_token)

    assert pilot_profile["profile_version"] == "v2"
    pilot_properties, _composed, pilot_errors, _warnings = module.compose_profile(
        pilot_profile,
        {
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
        },
    )
    assert pilot_errors == []
    assert pilot_properties["specification"] == "PILOT-12-1200x600"


def test_profile_config_preview_returns_preview_and_diagnostics():
    module = _load_plugin_module()
    result = module.preview_profile_config(
        {
            "profiles": {
                "sheet": {
                    "active_version": "v2",
                    "versions": {
                        "v1": {
                            "compose": {"template": "{length}*{width}*{thickness}"},
                        },
                        "v2": {
                            "compose": {"template": "PL{thickness}-{length}x{width}"},
                            "cad_mapping": {"specification": "物料规格"},
                        },
                    },
                }
            }
        },
        profile_id="sheet",
        values={
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
        },
    )

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["profile"]["profile_version"] == "v2"
    assert result["preview"]["properties"]["specification"] == "PL12-1200x600"
    assert result["preview"]["cad_fields"]["物料规格"] == "PL12-1200x600"
    assert {profile["profile_id"] for profile in result["profiles"]} >= {
        "sheet",
        "tube",
        "bar",
        "forging",
    }


def test_profile_config_preview_reports_unknown_template_field():
    module = _load_plugin_module()

    result = module.preview_profile_config(
        {
            "profiles": {
                "sheet": {
                    "compose": {"template": "{missing_length}*{width}"},
                }
            }
        },
        profile_id="sheet",
        values={"material": "Q235B", "width": 600, "thickness": 12},
    )

    assert result["ok"] is False
    assert any(
        error["code"] == "unknown_template_field"
        and error["profile_id"] == "sheet"
        and "missing_length" in error["message"]
        for error in result["errors"]
    )
    assert any(
        error["code"] == "compose_failed"
        for error in result["errors"]
    )


def test_config_preview_route_can_hide_full_profile_list():
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/plugins/cad-material-sync/config/preview",
        json={
            "profile_id": "sheet",
            "include_profiles": False,
            "config": {
                "profiles": {
                    "sheet": {
                        "compose": {"template": "T{thickness}-{length}x{width}"},
                    }
                }
            },
            "values": {
                "material": "Q235B",
                "length": 1200,
                "width": 600,
                "thickness": 12,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["profiles"] == []
    assert payload["profile"]["profile_id"] == "sheet"
    assert payload["preview"]["properties"]["specification"] == "T12-1200x600"


def test_config_preview_route_can_render_target_cad_system_fields():
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/plugins/cad-material-sync/config/preview",
        json={
            "profile_id": "sheet",
            "include_profiles": False,
            "cad_system": "solidworks",
            "config": {
                "profiles": {
                    "sheet": {
                        "fields": [
                            {
                                "name": "material",
                                "type": "string",
                                "required": True,
                                "cad_key": "材料",
                                "cad_key_by_connector": {
                                    "solidworks": "SW-Material@Part"
                                },
                            },
                            {
                                "name": "length",
                                "type": "number",
                                "required": True,
                                "unit": "mm",
                                "cad_key": "长",
                                "cad_key_by_connector": {
                                    "solidworks": "SW-Length@Part"
                                },
                            },
                            {
                                "name": "width",
                                "type": "number",
                                "required": True,
                                "unit": "mm",
                                "cad_key": "宽",
                                "cad_key_by_connector": {
                                    "solidworks": "SW-Width@Part"
                                },
                            },
                            {
                                "name": "thickness",
                                "type": "number",
                                "required": True,
                                "unit": "mm",
                                "cad_key": "厚",
                                "cad_key_by_connector": {
                                    "solidworks": "SW-Thickness@Part"
                                },
                            },
                        ],
                        "cad_mapping": {
                            "specification": {
                                "default": "规格",
                                "solidworks": "SW-Specification@Part",
                            }
                        },
                    }
                }
            },
            "values": {
                "material": "Q235B",
                "length": 1200,
                "width": 600,
                "thickness": 12,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["preview"]["cad_fields"] == {
        "SW-Material@Part": "Q235B",
        "SW-Length@Part": 1200,
        "SW-Width@Part": 600,
        "SW-Thickness@Part": 12,
        "SW-Specification@Part": "1200*600*12",
    }


def test_config_routes_persist_validate_and_delete_profile_config(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)

    invalid = client.put(
        "/api/v1/plugins/cad-material-sync/config",
        json={
            "config": {
                "profiles": {
                    "sheet": {
                        "compose": {"template": "{missing_length}*{width}"},
                    }
                }
            }
        },
    )

    assert invalid.status_code == 200
    invalid_payload = invalid.json()
    assert invalid_payload["ok"] is False
    assert invalid_payload["saved"] is False
    assert any(
        error["code"] == "unknown_template_field"
        for error in invalid_payload["errors"]
    )
    assert material_sync_session.query(PluginConfig).count() == 0

    save = client.put(
        "/api/v1/plugins/cad-material-sync/config",
        json={
            "config": {
                "profiles": {
                    "sheet": {
                        "compose": {"template": "T{thickness}-{length}x{width}"},
                        "cad_mapping": {"specification": "物料规格"},
                    }
                }
            }
        },
    )

    assert save.status_code == 200
    save_payload = save.json()
    assert save_payload["ok"] is True
    assert save_payload["saved"] is True
    assert save_payload["scope"] == {"tenant_id": "default", "org_id": "default"}
    assert save_payload["config"]["profiles"]["sheet"]["cad_mapping"] == {
        "specification": "物料规格"
    }
    assert material_sync_session.query(PluginConfig).count() == 1

    get_response = client.get("/api/v1/plugins/cad-material-sync/config")
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["ok"] is True
    sheet_profile = next(
        profile for profile in get_payload["profiles"] if profile["profile_id"] == "sheet"
    )
    assert sheet_profile["compose"]["template"] == "T{thickness}-{length}x{width}"

    delete = client.delete("/api/v1/plugins/cad-material-sync/config")
    assert delete.status_code == 200
    delete_payload = delete.json()
    assert delete_payload["ok"] is True
    assert delete_payload["deleted"] is True
    assert material_sync_session.query(PluginConfig).count() == 0


def test_config_write_routes_require_admin(material_sync_session):
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: material_sync_session
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(
        id="2",
        roles=["engineer"],
        is_superuser=False,
    )
    client = TestClient(app)

    save = client.put(
        "/api/v1/plugins/cad-material-sync/config",
        json={"config": {"profiles": {"sheet": {"compose": {"template": "{length}"}}}}},
    )
    delete = client.delete("/api/v1/plugins/cad-material-sync/config")

    assert save.status_code == 403
    assert delete.status_code == 403
    assert material_sync_session.query(PluginConfig).count() == 0


def test_config_export_import_bundle_round_trip_and_dry_run(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    config = {
        "profiles": {
            "sheet": {
                "compose": {"template": "T{thickness}-{length}x{width}"},
                "cad_mapping": {"specification": "物料规格"},
            }
        }
    }

    save = client.put(
        "/api/v1/plugins/cad-material-sync/config",
        json={"config": config},
    )
    assert save.status_code == 200
    assert save.json()["ok"] is True

    export = client.get("/api/v1/plugins/cad-material-sync/config/export")
    assert export.status_code == 200
    export_payload = export.json()
    assert export_payload["ok"] is True
    bundle = export_payload["bundle"]
    assert bundle["plugin_id"] == module.PLUGIN_ID
    assert bundle["schema_version"] == 1
    assert bundle["config"] == config
    assert bundle["config_hash"] == module._stable_config_hash(config)

    delete = client.delete("/api/v1/plugins/cad-material-sync/config")
    assert delete.status_code == 200
    assert material_sync_session.query(PluginConfig).count() == 0

    dry_run = client.post(
        "/api/v1/plugins/cad-material-sync/config/import",
        json={"bundle": bundle, "dry_run": True},
    )
    assert dry_run.status_code == 200
    dry_run_payload = dry_run.json()
    assert dry_run_payload["ok"] is True
    assert dry_run_payload["dry_run"] is True
    assert dry_run_payload["imported"] is False
    assert material_sync_session.query(PluginConfig).count() == 0

    imported = client.post(
        "/api/v1/plugins/cad-material-sync/config/import",
        json={"bundle": bundle},
    )
    assert imported.status_code == 200
    imported_payload = imported.json()
    assert imported_payload["ok"] is True
    assert imported_payload["imported"] is True
    assert imported_payload["config"] == config
    assert material_sync_session.query(PluginConfig).count() == 1


def test_config_import_rejects_tampered_bundle_and_requires_admin(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    bundle = {
        "schema_version": 1,
        "plugin_id": module.PLUGIN_ID,
        "config_hash": "bad-hash",
        "config": {
            "profiles": {
                "sheet": {
                    "compose": {"template": "T{thickness}-{length}x{width}"},
                }
            }
        },
    }

    rejected = client.post(
        "/api/v1/plugins/cad-material-sync/config/import",
        json={"bundle": bundle},
    )
    assert rejected.status_code == 200
    rejected_payload = rejected.json()
    assert rejected_payload["ok"] is False
    assert rejected_payload["imported"] is False
    assert any(
        error["code"] == "config_hash_mismatch"
        for error in rejected_payload["errors"]
    )
    assert material_sync_session.query(PluginConfig).count() == 0

    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: material_sync_session
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(
        id="2",
        roles=["engineer"],
        is_superuser=False,
    )
    non_admin_client = TestClient(app)
    valid_bundle = {
        **bundle,
        "config_hash": module._stable_config_hash(bundle["config"]),
    }
    forbidden = non_admin_client.post(
        "/api/v1/plugins/cad-material-sync/config/import",
        json={"bundle": valid_bundle},
    )
    assert forbidden.status_code == 403
    assert material_sync_session.query(PluginConfig).count() == 0


def test_cad_diff_preview_reports_field_level_changes():
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/plugins/cad-material-sync/diff/preview",
        json={
            "profile_id": "sheet",
            "current_cad_fields": {
                "物料类别": "sheet",
                "材料": "Q235B",
                "长": "1200",
                "宽": "600",
                "厚": "",
                "规格": "旧规格",
            },
            "values": {
                "material": "Q235B",
                "length": 1200,
                "width": 600,
                "thickness": 12,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["summary"] == {
        "added": 1,
        "changed": 1,
        "cleared": 0,
        "unchanged": 4,
    }
    diffs = {diff["cad_key"]: diff for diff in payload["diffs"]}
    assert diffs["厚"] == {
        "cad_key": "厚",
        "property": "thickness",
        "current": "",
        "target": 12,
        "status": "added",
    }
    assert diffs["规格"]["status"] == "changed"
    assert diffs["规格"]["current"] == "旧规格"
    assert diffs["规格"]["target"] == "1200*600*12"
    assert diffs["长"]["status"] == "unchanged"
    assert payload["target_cad_fields"]["规格"] == "1200*600*12"
    assert payload["write_cad_fields"] == {"厚": 12, "规格": "1200*600*12"}
    assert payload["requires_confirmation"] is True


def test_diff_preview_with_item_id_uses_selected_item_properties(material_sync_session):
    # Phase 4 bind contract: when item_id is supplied, /diff/preview targets the
    # SELECTED Item.properties, not the current CAD fields (and never the
    # assistant resolve draft package). Current CAD here conflicts with the item.
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    _add_part(
        material_sync_session,
        "bind-target",
        {
            "material_category": "sheet",
            "material": "Q345B",
            "length": 1200,
            "width": 600,
            "thickness": 10,
            "specification": "1200*600*10",
            "name": "板A",
        },
    )

    resp = client.post(
        "/api/v1/plugins/cad-material-sync/diff/preview",
        json={
            "profile_id": "sheet",
            "item_id": "bind-target",
            "current_cad_fields": {
                "物料类别": "sheet",
                "材料": "Q235B",
                "长": "1200",
                "宽": "600",
                "厚": "12",
                "规格": "旧规格",
            },
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    # target/write come from the selected item, not the conflicting current CAD
    assert payload["target_cad_fields"]["材料"] == "Q345B"
    assert payload["target_cad_fields"]["规格"] == "1200*600*10"
    assert payload["write_cad_fields"]["材料"] == "Q345B"
    assert payload["write_cad_fields"]["厚"] == 10
    assert payload["write_cad_fields"]["规格"] == "1200*600*10"
    assert payload["requires_confirmation"] is True


def test_diff_preview_missing_item_returns_404(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    resp = client.post(
        "/api/v1/plugins/cad-material-sync/diff/preview",
        json={"profile_id": "sheet", "item_id": "does-not-exist", "current_cad_fields": {}},
    )
    assert resp.status_code == 404


def test_cad_diff_preview_can_compare_explicit_target_cad_fields():
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/plugins/cad-material-sync/diff/preview",
        json={
            "profile_id": "sheet",
            "current_cad_fields": {"备注": "旧备注"},
            "target_cad_fields": {"备注": ""},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["summary"] == {
        "added": 0,
        "changed": 0,
        "cleared": 1,
        "unchanged": 0,
    }
    assert payload["diffs"] == [
        {
            "cad_key": "备注",
            "property": None,
            "current": "旧备注",
            "target": "",
            "status": "cleared",
        }
    ]
    assert payload["write_cad_fields"] == {"备注": ""}
    assert payload["requires_confirmation"] is True


def test_cad_diff_preview_without_changes_requires_no_confirmation():
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/plugins/cad-material-sync/diff/preview",
        json={
            "profile_id": "sheet",
            "current_cad_fields": {"材料": "Q235B"},
            "target_cad_fields": {"材料": "Q235B"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["summary"] == {
        "added": 0,
        "changed": 0,
        "cleared": 0,
        "unchanged": 1,
    }
    assert payload["write_cad_fields"] == {}
    assert payload["requires_confirmation"] is False


def test_cad_diff_confirm_contract_fixture_cases():
    module = _load_plugin_module()
    root = Path(__file__).resolve().parents[4]
    fixture = json.loads(
        (root / "docs" / "samples" / "cad_material_diff_confirm_fixture.json").read_text()
    )
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    for case in fixture["cases"]:
        response = client.post(
            "/api/v1/plugins/cad-material-sync/diff/preview",
            json=case["request"],
        )

        assert response.status_code == 200, case["name"]
        payload = response.json()
        expected = case["expect"]
        assert payload["ok"] is expected["ok"], case["name"]
        assert payload["summary"] == expected["summary"], case["name"]
        assert payload["write_cad_fields"] == expected["write_cad_fields"], case["name"]
        assert payload["requires_confirmation"] is expected["requires_confirmation"], case["name"]
        diffs = {diff["cad_key"]: diff for diff in payload["diffs"]}
        for cad_key, status in expected["statuses"].items():
            assert diffs[cad_key]["status"] == status, case["name"]


def test_profile_unit_conversion_and_display_format():
    module = _load_plugin_module()
    profiles = module.load_profiles(
        config={
            "profiles": {
                "sheet": {
                    "fields": [
                        {
                            "name": "length",
                            "label": "长",
                            "type": "number",
                            "required": True,
                            "unit": "mm",
                            "display_unit": "cm",
                            "display_precision": 1,
                        },
                        {
                            "name": "width",
                            "label": "宽",
                            "type": "number",
                            "required": True,
                            "unit": "mm",
                            "display_unit": "cm",
                            "display_precision": 1,
                        },
                        {
                            "name": "thickness",
                            "label": "厚",
                            "type": "number",
                            "required": True,
                            "unit": "mm",
                            "display_precision": 1,
                            "display_suffix": "mm",
                        },
                    ],
                    "compose": {"template": "{length}cm*{width}cm*{thickness}"},
                },
            }
        }
    )

    properties, composed, errors, warnings = module.compose_profile(
        profiles["sheet"],
        {
            "material": "Q235B",
            "length": {"value": 1.2, "unit": "m"},
            "width": "60cm",
            "thickness": "0.5in",
        },
    )

    assert errors == []
    assert warnings == []
    assert properties["length"] == 1200
    assert properties["width"] == 600
    assert properties["thickness"] == 12.7
    assert composed == {"specification": "120cm*60cm*12.7mm"}
    assert properties["specification"] == "120cm*60cm*12.7mm"


def test_profile_unit_conversion_reports_invalid_unit():
    module = _load_plugin_module()
    profile = module.load_profiles(
        config={
            "profiles": {
                "sheet": {
                    "fields": [
                        {
                            "name": "length",
                            "type": "number",
                            "required": True,
                            "unit": "kg",
                        }
                    ]
                }
            }
        }
    )["sheet"]

    properties, _composed, errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "Q235B",
            "length": "1200mm",
            "width": 600,
            "thickness": 12,
        },
    )

    assert properties["width"] == 600
    assert any(
        error["field"] == "length" and error["code"] == "invalid_unit"
        for error in errors
    )


def test_conditional_required_field_only_applies_when_condition_matches():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["sheet"]
    profile["fields"].append(
        {
            "name": "plate_standard",
            "label": "板材标准",
            "type": "string",
            "required": True,
            "cad_key": "板材标准",
            "when": {
                "all": [
                    {"field": "material_category", "equals": "sheet"},
                    {"field": "material", "in": ["Q235B", "Q355B"]},
                ]
            },
        }
    )

    missing_standard, _composed, errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
        },
    )

    assert missing_standard["material_category"] == "sheet"
    assert missing_standard["specification"] == "1200*600*12"
    assert any(
        error["field"] == "plate_standard" and error["code"] == "required"
        for error in errors
    )

    optional_standard, _composed, optional_errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "6061",
            "length": 1200,
            "width": 600,
            "thickness": 12,
        },
    )

    assert optional_errors == []
    assert optional_standard["specification"] == "1200*600*12"
    assert "plate_standard" not in optional_standard

    with_standard, _composed, with_errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "Q355B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
            "plate_standard": "GB/T 3274",
        },
    )

    assert with_errors == []
    assert with_standard["plate_standard"] == "GB/T 3274"
    assert module.cad_field_package(profile, with_standard)["板材标准"] == "GB/T 3274"


def test_required_when_can_make_forging_attribute_contextual():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["forging"]
    profile["fields"].append(
        {
            "name": "heat_treatment_standard",
            "label": "热处理标准",
            "type": "string",
            "required_when": {"field": "heat_treatment", "exists": True},
            "cad_key": "热处理标准",
        }
    )

    without_heat_treatment, _composed, optional_errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "42CrMo",
            "blank_size": "200*150*80",
        },
    )

    assert optional_errors == []
    assert without_heat_treatment["material_category"] == "forging"
    assert "heat_treatment_standard" not in without_heat_treatment

    missing_standard, _composed, errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "42CrMo",
            "blank_size": "200*150*80",
            "heat_treatment": "QT",
        },
    )

    assert any(
        error["field"] == "heat_treatment_standard" and error["code"] == "required"
        for error in errors
    )

    with_standard, _composed, with_errors, _warnings = module.compose_profile(
        profile,
        {
            "material": "42CrMo",
            "blank_size": "200*150*80",
            "heat_treatment": "QT",
            "heat_treatment_standard": "GB/T 3077",
        },
    )

    assert with_errors == []
    assert with_standard["heat_treatment_standard"] == "GB/T 3077"


def test_build_updates_fills_empty_by_default_and_reports_conflicts():
    module = _load_plugin_module()

    updates, conflicts = module._build_updates(
        {"material": "Q235", "specification": "old", "length": ""},
        {"material": "Q345", "specification": "new", "length": 1200},
        overwrite=False,
    )

    assert updates == {"length": 1200}
    assert {conflict["field"] for conflict in conflicts} == {"material", "specification"}

    updates, conflicts = module._build_updates(
        {"material": "Q235", "specification": "old", "length": ""},
        {"material": "Q345", "specification": "new", "length": 1200},
        overwrite=True,
    )

    assert updates == {
        "material": "Q345",
        "specification": "new",
        "length": 1200,
    }
    assert conflicts == []


def test_profile_default_overwrite_reads_sync_defaults_only():
    module = _load_plugin_module()

    assert module._profile_default_overwrite(
        {"profile_id": "sheet", "ui_defaults": {"overwrite": True}}
    ) == (False, None, None)
    assert module._profile_default_overwrite(
        {"profile_id": "sheet", "sync_defaults": {"overwrite": "true"}}
    ) == (True, "sync_defaults.overwrite", None)

    default_overwrite, source, warning = module._profile_default_overwrite(
        {"profile_id": "sheet", "sync_defaults": {"overwrite": "always"}}
    )

    assert default_overwrite is False
    assert source == "sync_defaults.overwrite"
    assert warning == "profile:sheet: sync_defaults.overwrite must be boolean"


def test_compose_route_returns_cad_field_package():
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/plugins/cad-material-sync/compose",
        json={
            "profile_id": "sheet",
            "values": {
                "material": "Q235",
                "length": 1200,
                "width": 600,
                "thickness": 8,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["profile_id"] == "sheet"
    assert payload["properties"]["specification"] == "1200*600*8"
    assert payload["cad_fields"]["规格"] == "1200*600*8"


def test_validate_route_keeps_existing_material_as_valid_candidate(monkeypatch):
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    monkeypatch.setattr(
        module,
        "_find_matching_items",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                id="item-1",
                item_type_id="Part",
                state="Released",
                properties={
                    "material": "Q235",
                    "material_category": "sheet",
                    "specification": "1200*600*8",
                },
            )
        ],
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/plugins/cad-material-sync/validate",
        json={
            "profile_id": "sheet",
            "values": {
                "material": "Q235",
                "length": 1200,
                "width": 600,
                "thickness": 8,
            },
            "lookup_existing": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["valid"] is True
    assert payload["matched_items"][0]["id"] == "item-1"


def test_sync_inbound_route_can_dry_run_new_item_from_cad_fields():
    module = _load_plugin_module()
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[module._get_db] = lambda: object()
    app.dependency_overrides[module._current_user] = lambda: SimpleNamespace(id="user-1")
    client = TestClient(app)

    response = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": {
                "材料": "Q235",
                "长": "1200",
                "宽": "600",
                "厚": "8",
            },
            "create_if_missing": True,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["action"] == "created"
    assert payload["dry_run"] is True
    assert payload["properties"]["specification"] == "1200*600*8"
    assert payload["cad_fields"]["规格"] == "1200*600*8"


def test_sync_inbound_can_create_update_and_outbound_from_real_sqlite_db(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)

    dry_run = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": {
                "图号": "DRW-1001",
                "名称": "支撑板",
                "材料": "Q235B",
                "物料类别": "sheet",
                "长": "1200",
                "宽": "600",
                "厚": "12",
                "规格": "旧规格",
            },
            "create_if_missing": True,
            "dry_run": True,
        },
    )

    assert dry_run.status_code == 200
    dry_run_payload = dry_run.json()
    assert dry_run_payload["ok"] is True
    assert dry_run_payload["action"] == "created"
    assert dry_run_payload["dry_run"] is True
    assert dry_run_payload["properties"]["specification"] == "1200*600*12"
    assert dry_run_payload["warnings"] == [
        "derived_field_mismatch:specification: input value "
        "'旧规格' was replaced by '1200*600*12'"
    ]
    assert material_sync_session.query(Item).count() == 0

    create = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": dry_run_payload["cad_fields"],
            "create_if_missing": True,
            "dry_run": False,
        },
    )

    assert create.status_code == 200
    create_payload = create.json()
    assert create_payload["ok"] is True
    assert create_payload["action"] == "created"
    assert create_payload["item_id"]

    created_item = material_sync_session.get(Item, create_payload["item_id"])
    assert created_item is not None
    assert created_item.item_type_id == "Part"
    assert created_item.properties["material"] == "Q235B"
    assert created_item.properties["specification"] == "1200*600*12"

    update = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "item_id": create_payload["item_id"],
            "cad_fields": {
                **dry_run_payload["cad_fields"],
                "材料": "Q355B",
            },
            "overwrite": True,
            "dry_run": False,
        },
    )

    assert update.status_code == 200
    update_payload = update.json()
    assert update_payload["ok"] is True
    assert update_payload["action"] == "updated"
    assert update_payload["updates"]["material"] == "Q355B"

    material_sync_session.refresh(created_item)
    assert created_item.properties["material"] == "Q355B"
    assert created_item.properties["specification"] == "1200*600*12"

    outbound = client.post(
        "/api/v1/plugins/cad-material-sync/sync/outbound",
        json={
            "profile_id": "sheet",
            "item_id": create_payload["item_id"],
            "include_empty": False,
        },
    )

    assert outbound.status_code == 200
    outbound_payload = outbound.json()
    assert outbound_payload["ok"] is True
    assert outbound_payload["cad_fields"]["材料"] == "Q355B"
    assert outbound_payload["cad_fields"]["规格"] == "1200*600*12"


def test_match_strategy_prefers_exact_identifier_and_reports_conflicts(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    existing = _add_part(
        material_sync_session,
        "part-drawing-1001",
        {
            "item_number": "DRW-1001",
            "name": "支撑板",
            "material_category": "sheet",
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
            "specification": "1200*600*12",
        },
    )

    response = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": {
                "图号": "DRW-1001",
                "名称": "支撑板",
                "材料": "Q355B",
                "物料类别": "sheet",
                "长": "1200",
                "宽": "600",
                "厚": "12",
            },
            "overwrite": False,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["action"] == "conflict"
    assert payload["item_id"] == existing.id
    assert payload["matched_items"][0]["id"] == existing.id
    assert {conflict["field"] for conflict in payload["conflicts"]} == {"material"}
    assert material_sync_session.get(Item, existing.id).properties["material"] == "Q235B"


def test_sync_inbound_non_dry_run_conflict_does_not_update_existing_item(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    existing = _add_part(
        material_sync_session,
        "part-conflict-guard",
        {
            "item_number": "DRW-CONFLICT-1",
            "material_category": "sheet",
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
            "specification": "1200*600*12",
        },
    )

    response = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": {
                "图号": "DRW-CONFLICT-1",
                "材料": "Q355B",
                "物料类别": "sheet",
                "长": "1200",
                "宽": "600",
                "厚": "12",
            },
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["action"] == "conflict"
    assert payload["item_id"] == existing.id
    assert payload["conflicts"] == [
        {"field": "material", "current": "Q235B", "incoming": "Q355B"}
    ]
    material_sync_session.refresh(existing)
    assert existing.properties["material"] == "Q235B"


def test_sync_inbound_uses_profile_sync_default_overwrite_when_request_omits_flag(
    material_sync_session,
):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    existing = _add_part(
        material_sync_session,
        "part-sync-default-overwrite",
        {
            "item_number": "DRW-SYNC-DEFAULT-1",
            "material_category": "sheet",
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
            "specification": "1200*600*12",
        },
    )

    save = client.put(
        "/api/v1/plugins/cad-material-sync/config",
        json={"config": {"profiles": {"sheet": {"sync_defaults": {"overwrite": True}}}}},
    )
    assert save.status_code == 200
    assert save.json()["ok"] is True

    response = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": {
                "图号": "DRW-SYNC-DEFAULT-1",
                "材料": "Q355B",
                "物料类别": "sheet",
                "长": "1200",
                "宽": "600",
                "厚": "12",
            },
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["action"] == "updated"
    assert payload["updates"]["material"] == "Q355B"
    assert payload["warnings"] == [
        "profile_default_overwrite_applied:sync_defaults.overwrite"
    ]
    material_sync_session.refresh(existing)
    assert existing.properties["material"] == "Q355B"


def test_sync_inbound_explicit_false_overrides_profile_sync_default(
    material_sync_session,
):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    existing = _add_part(
        material_sync_session,
        "part-sync-default-explicit-false",
        {
            "item_number": "DRW-SYNC-DEFAULT-2",
            "material_category": "sheet",
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
            "specification": "1200*600*12",
        },
    )

    save = client.put(
        "/api/v1/plugins/cad-material-sync/config",
        json={"config": {"profiles": {"sheet": {"sync_defaults": {"overwrite": True}}}}},
    )
    assert save.status_code == 200
    assert save.json()["ok"] is True

    response = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": {
                "图号": "DRW-SYNC-DEFAULT-2",
                "材料": "Q355B",
                "物料类别": "sheet",
                "长": "1200",
                "宽": "600",
                "厚": "12",
            },
            "overwrite": False,
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["action"] == "conflict"
    assert payload["warnings"] == []
    material_sync_session.refresh(existing)
    assert existing.properties["material"] == "Q235B"


def test_sync_inbound_ignores_ui_default_overwrite_for_server_writes(
    material_sync_session,
):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    existing = _add_part(
        material_sync_session,
        "part-ui-default-ignored",
        {
            "item_number": "DRW-UI-DEFAULT-1",
            "material_category": "sheet",
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
            "specification": "1200*600*12",
        },
    )

    save = client.put(
        "/api/v1/plugins/cad-material-sync/config",
        json={"config": {"profiles": {"sheet": {"ui_defaults": {"overwrite": True}}}}},
    )
    assert save.status_code == 200
    assert save.json()["ok"] is True

    response = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": {
                "图号": "DRW-UI-DEFAULT-1",
                "材料": "Q355B",
                "物料类别": "sheet",
                "长": "1200",
                "宽": "600",
                "厚": "12",
            },
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["action"] == "conflict"
    assert payload["warnings"] == []
    material_sync_session.refresh(existing)
    assert existing.properties["material"] == "Q235B"


def test_validate_lookup_existing_uses_material_code_priority(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    _add_part(
        material_sync_session,
        "part-material-code-001",
        {
            "material_code": "MAT-SHEET-Q235B-12",
            "material_category": "sheet",
            "material": "Q235B",
            "length": 1200,
            "width": 600,
            "thickness": 12,
            "specification": "1200*600*12",
        },
    )

    response = client.post(
        "/api/v1/plugins/cad-material-sync/validate",
        json={
            "profile_id": "sheet",
            "values": {
                "material_code": "MAT-SHEET-Q235B-12",
                "material": "Q235B",
                "length": 1200,
                "width": 600,
                "thickness": 12,
            },
            "lookup_existing": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["valid"] is True
    assert [item["id"] for item in payload["matched_items"]] == ["part-material-code-001"]


def test_sync_inbound_returns_ambiguous_match_candidates(material_sync_session):
    module = _load_plugin_module()
    client = _db_client(module, material_sync_session)
    for index in range(2):
        _add_part(
            material_sync_session,
            f"part-ambiguous-{index}",
            {
                "material_category": "sheet",
                "material": "Q235B",
                "length": 1200,
                "width": 600,
                "thickness": 12,
                "specification": "1200*600*12",
            },
        )

    response = client.post(
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        json={
            "profile_id": "sheet",
            "cad_fields": {
                "材料": "Q235B",
                "物料类别": "sheet",
                "长": "1200",
                "宽": "600",
                "厚": "12",
            },
            "create_if_missing": True,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["action"] == "ambiguous_match"
    assert {item["id"] for item in payload["matched_items"]} == {
        "part-ambiguous-0",
        "part-ambiguous-1",
    }
    assert material_sync_session.query(Item).count() == 2


# ==========================================================================
# finishing/treatment R1 (taskbook #689) — optional `finish` field + the
# `finish_standard` companion, on the existing material-sync profile system.
# ==========================================================================

_SHEET_BASE = {"material": "AL-7075", "length": 10, "width": 5, "thickness": 2}


def test_finish_is_optional_on_all_default_profiles_and_packages_to_cad():
    module = _load_plugin_module()
    profiles = module.load_profiles(config={})
    for profile_id in ("sheet", "tube", "bar", "forging"):
        profile = profiles[profile_id]
        names = {f["name"] for f in profile["fields"] if isinstance(f, dict)}
        assert "finish" in names, f"{profile_id} is missing the optional finish field"
        # non-empty finish packages back to its CAD key
        package = module.cad_field_package(profile, {"finish": "hard-anodized"})
        assert package.get("表面处理") == "hard-anodized"


def test_blank_finish_is_omitted_from_cad_package_by_default():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["sheet"]
    assert "表面处理" not in module.cad_field_package(profile, {"finish": ""})


def test_finish_enum_rejects_unmapped_value():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["sheet"]
    for field in profile["fields"]:
        if field.get("name") == "finish":
            field["enum"] = ["anodized", "painted"]
    _vals, _composed, errors, _warnings = module.compose_profile(
        profile, {**_SHEET_BASE, "finish": "chromed"}
    )
    assert any(
        e["field"] == "finish" and e["code"] == "invalid_enum" for e in errors
    )


def test_finish_standard_required_only_when_finish_exists():
    module = _load_plugin_module()
    profile = module.load_profiles(config={})["sheet"]
    profile["fields"].append(
        {
            "name": "finish_standard",
            "label": "表面处理标准",
            "type": "string",
            "required_when": {"field": "finish", "exists": True},
            "cad_key": "表面处理标准",
        }
    )
    # finish absent -> finish_standard not required
    _v, _c, errors, _w = module.compose_profile(profile, dict(_SHEET_BASE))
    assert not any(e["field"] == "finish_standard" for e in errors)
    # finish present, standard absent -> required error
    _v, _c, errors, _w = module.compose_profile(
        profile, {**_SHEET_BASE, "finish": "anodized"}
    )
    assert any(
        e["field"] == "finish_standard" and e["code"] == "required" for e in errors
    )
    # both present -> clean
    vals, _c, errors, _w = module.compose_profile(
        profile, {**_SHEET_BASE, "finish": "anodized", "finish_standard": "GB/T 1031"}
    )
    assert not any(e["field"] == "finish_standard" for e in errors)
    assert vals["finish"] == "anodized"
