# SPDX-License-Identifier: MIT
"""Conformance-oriented tests: synthetic UDT export -> AASX -> round-trip."""
import json
import os
import tempfile

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

from ippr_shell import udt2aas

SYNTH_TYPES = {
    "name": "", "tagType": "Provider", "tags": [
        {"name": "_types_", "tagType": "Folder", "tags": [
            {"name": "RP-01:Press", "tagType": "UdtType", "tags": [
                {"name": "INFORMATIONS", "tagType": "Folder", "tags": [
                    {"name": "Designation", "tagType": "AtomicTag", "dataType": "String"},
                ]},
                {"name": "PROCESS", "tagType": "Folder", "tags": [
                    {"name": "STATES", "tagType": "Folder", "tags": [
                        {"name": "Running", "tagType": "AtomicTag", "dataType": "Boolean"},
                        {"name": "Takt_Time", "tagType": "AtomicTag", "dataType": "Int4"},
                    ]},
                ]},
            ]},
        ]},
    ],
}

SYNTH_INSTANCES = {
    "name": "", "tagType": "Provider", "tags": [
        {"name": "RESOURCES", "tagType": "Folder", "tags": [
            {"name": "PHYSICAL", "tagType": "Folder", "tags": [
                {"name": "RP-01-PRESS-1", "tagType": "UdtInstance",
                 "typeId": "RP-01:Press", "tags": []},
            ]},
        ]},
        {"name": "PRODUCTS", "tagType": "Folder", "tags": [
            {"name": "MATERIALS", "tagType": "Folder", "tags": [
                {"name": "Bom", "tagType": "AtomicTag", "dataType": "Document"},
            ]},
        ]},
    ],
}


def _write(tmp, name, data):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return p


def test_convert_and_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        t = _write(tmp, "types.json", SYNTH_TYPES)
        i = _write(tmp, "instances.json", SYNTH_INSTANCES)
        res = udt2aas.convert(t, i, "https://example.com/test", "TestCo")
        assert res.stats["types"] == 1
        assert res.stats["instances"] == 1
        aasx = os.path.join(tmp, "out.aasx")
        udt2aas.write_outputs(res, aasx, os.path.join(tmp, "out.json"))

        store = model.DictObjectStore()
        with AASXReader(aasx) as r:
            r.read_into(store, DictSupplementaryFileContainer())
        shells = [o for o in store if isinstance(o, model.AssetAdministrationShell)]
        assert len(shells) == 3  # type + instance + enterprise
        kinds = {s.asset_information.asset_kind for s in shells}
        assert model.AssetKind.TYPE in kinds and model.AssetKind.INSTANCE in kinds
        inst = next(s for s in shells
                    if s.asset_information.asset_kind == model.AssetKind.INSTANCE
                    and not (s.id_short or "").startswith("ES_"))
        assert inst.asset_information.asset_type  # typed instance (fractal link)


def test_sanitize_id_short():
    assert udt2aas.sanitize_id_short("RP ou F-code:name") == "RP_ou_F_code_name"
    assert udt2aas.sanitize_id_short("1 IDENTIFICATION").startswith("N_")
    used = set()
    a = udt2aas.sanitize_id_short("x", used)
    b = udt2aas.sanitize_id_short("x", used)
    assert a != b
