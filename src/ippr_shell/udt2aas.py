# SPDX-License-Identifier: MIT
"""
udt2aas — Convert Ignition UDT/tag JSON exports into AAS (IEC 63278) packages.

Part of IPPR Shell, the open-source toolchain for the Fractal IPPR method
(Eric Truffet): Information / Product / Process / Resources, recursively
applied to every system of the enterprise.

Mapping (validated by the method author):
    Ignition UdtType      ->  AAS with assetKind = TYPE      (= SysML block)
    Ignition UdtInstance  ->  AAS with assetKind = INSTANCE  (= block instance)
    Folder hierarchy      ->  Submodel / SubmodelElementCollection tree
    AtomicTag             ->  Property (typed)
    Tag path              ->  kept as 'sourcePath' property (traceability)

The enterprise tag tree (IPPR grid: INFORMATIONS / PRODUCTS / PROCESS /
RESOURCES) becomes one "enterprise" AAS whose submodels are the four IPPR
branches.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXWriter, DictSupplementaryFileContainer
from basyx.aas.adapter.json import write_aas_json_file

IPPR_BRANCHES = ("INFORMATIONS", "PRODUCTS", "PROCESS", "RESOURCES")
RESOURCE_NATURES = ("PHYSICAL", "DIGITAL", "FUNCTIONNAL", "FUNCTIONAL")

# Ignition dataType -> (basyx value type, default value)
DATATYPE_MAP = {
    "Int1": model.datatypes.Int, "Int2": model.datatypes.Int,
    "Int4": model.datatypes.Int, "Int8": model.datatypes.Long,
    "Float4": model.datatypes.Float, "Float8": model.datatypes.Double,
    "Boolean": model.datatypes.Boolean,
    "String": model.datatypes.String, "Text": model.datatypes.String,
    "DateTime": model.datatypes.String,
    "Document": model.datatypes.String, "DataSet": model.datatypes.String,
}


SEMANTIC_NS = "https://ippr-shell.io/semantics/submodel"


def ippr_semantic_id(kind: str) -> model.ExternalReference:
    """Published semantic id for the (free/proprietary) IPPR submodel
    templates — AAS V3 allows proprietary submodels; giving them a stable,
    published semanticId is what makes them recognizable by other tools."""
    return model.ExternalReference(
        (model.Key(type_=model.KeyTypes.GLOBAL_REFERENCE,
                   value="%s/%s/1/0" % (SEMANTIC_NS, kind.lower())),))


def sanitize_id_short(name: str, used: Optional[set] = None) -> str:
    """AAS id_short: [A-Za-z][A-Za-z0-9_]*, <= 128 chars, unique in scope."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    s = re.sub(r"__+", "_", s).strip("_") or "unnamed"
    if not s[0].isalpha():
        s = "N_" + s
    s = s[:120]
    if used is not None:
        base, i = s, 1
        while s in used:
            i += 1
            s = "%s_%d" % (base[:115], i)
        used.add(s)
    return s


def load_export(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Tree walking helpers
# ---------------------------------------------------------------------------

def iter_nodes(node: dict, path: str = "") -> Iterator[tuple[str, dict]]:
    """Yield (path, node) for every node of an Ignition tag export tree."""
    name = node.get("name", "")
    here = (path + "/" + name).strip("/") if name else path
    yield here, node
    for child in node.get("tags", []):
        yield from iter_nodes(child, here)


def find_subtree(root: dict, name: str) -> Optional[dict]:
    for _, node in iter_nodes(root):
        if node.get("name") == name and node.get("tagType") == "Folder":
            return node
    return None


def ippr_classify(path: str) -> dict:
    """Derive IPPR branch / resource nature from a tag path."""
    parts = [p.upper() for p in path.split("/")]
    branch = next((b for b in IPPR_BRANCHES if b in parts), None)
    nature = next((n for n in RESOURCE_NATURES if n in parts), None)
    return {"branch": branch, "nature": nature}


# ---------------------------------------------------------------------------
# AAS construction
# ---------------------------------------------------------------------------

@dataclass
class ConversionResult:
    store: model.DictObjectStore = field(default_factory=model.DictObjectStore)
    aas_ids: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {
        "types": 0, "instances": 0, "properties": 0, "collections": 0})


class Udt2AasConverter:
    """Builds an AAS environment from Ignition type + instance exports."""

    def __init__(self, namespace: str, company: str):
        self.ns = namespace.rstrip("/")
        self.company = sanitize_id_short(company)
        self.result = ConversionResult()
        self._type_ids: dict[str, str] = {}   # typeId path -> AAS id

    # -- element builders ---------------------------------------------------

    def _prop(self, node: dict, used: set) -> model.Property:
        vt = DATATYPE_MAP.get(node.get("dataType", ""), model.datatypes.String)
        self.result.stats["properties"] += 1
        return model.Property(
            id_short=sanitize_id_short(node.get("name", "tag"), used),
            value_type=vt,
            display_name=model.MultiLanguageNameType({"en": node.get("name", "")[:64]}),
        )

    def _collection(self, node: dict, used: set, depth: int = 0
                    ) -> model.SubmodelElementCollection:
        inner_used: set = set()
        elements = []
        for child in node.get("tags", []):
            el = self._element(child, inner_used, depth + 1)
            if el is not None:
                elements.append(el)
        self.result.stats["collections"] += 1
        return model.SubmodelElementCollection(
            id_short=sanitize_id_short(node.get("name", "group"), used),
            value=elements,
            display_name=model.MultiLanguageNameType({"en": node.get("name", "")[:64]}),
        )

    def _element(self, node: dict, used: set, depth: int = 0):
        tt = node.get("tagType")
        if depth > 20:
            return None
        if tt == "AtomicTag":
            return self._prop(node, used)
        if tt == "Folder":
            return self._collection(node, used, depth)
        if tt == "UdtInstance":
            # Reference to the instance AAS (fractal recursion hook)
            ref = model.ReferenceElement(
                id_short=sanitize_id_short(node.get("name", "instance"), used),
                display_name=model.MultiLanguageNameType(
                    {"en": ("instance of " + node.get("typeId", "?"))[:64]}),
            )
            return ref
        if tt == "UdtType":
            return None  # types nested in tree are handled globally
        return None

    def _origin_submodel(self, sm_id: str, path: str, extra: dict) -> model.Submodel:
        used: set = set()
        cls = ippr_classify(path)
        props = [
            model.Property(id_short="sourcePath", value_type=model.datatypes.String,
                           value=path),
            model.Property(id_short="ipprBranch", value_type=model.datatypes.String,
                           value=cls["branch"] or "UNCLASSIFIED"),
        ]
        if cls["nature"]:
            props.append(model.Property(id_short="resourceNature",
                                        value_type=model.datatypes.String,
                                        value=cls["nature"]))
        for k, v in extra.items():
            props.append(model.Property(id_short=sanitize_id_short(k, used),
                                        value_type=model.datatypes.String,
                                        value=str(v)[:1000]))
        return model.Submodel(id_=sm_id, id_short="IPPROrigin",
                              semantic_id=ippr_semantic_id("ippr-origin"),
                              submodel_element=props)

    # -- main entries ---------------------------------------------------------

    def convert_type(self, node: dict, path: str) -> None:
        name = node.get("name", "UdtType")
        aas_id = "%s/type/%s" % (self.ns, sanitize_id_short(path))
        self._type_ids[path] = aas_id
        submodels = []
        used_sm: set = set()
        # each IPPR sub-branch of the UDT becomes a submodel (fractal grid)
        for child in node.get("tags", []):
            if child.get("tagType") == "Folder":
                used: set = set()
                sm = model.Submodel(
                    id_="%s/sm/%s" % (aas_id, sanitize_id_short(child["name"], used_sm)),
                    id_short=sanitize_id_short(child["name"], set()),
                    semantic_id=ippr_semantic_id(
                        "ippr-viewpoint-" + sanitize_id_short(child["name"])),
                    submodel_element=[
                        e for e in (self._element(c, used, 1)
                                    for c in child.get("tags", [])) if e is not None],
                )
                self.result.store.add(sm)
                submodels.append(model.ModelReference.from_referable(sm))
        origin = self._origin_submodel(aas_id + "/sm/IPPROrigin", path,
                                       {"ignitionTagType": "UdtType"})
        self.result.store.add(origin)
        submodels.append(model.ModelReference.from_referable(origin))
        aas = model.AssetAdministrationShell(
            id_=aas_id,
            id_short=sanitize_id_short(name),
            display_name=model.MultiLanguageNameType({"en": name[:64]}),
            asset_information=model.AssetInformation(
                asset_kind=model.AssetKind.TYPE,
                global_asset_id="%s/asset/type/%s" % (self.ns, sanitize_id_short(path)),
            ),
            submodel=set(submodels),
        )
        self.result.store.add(aas)
        self.result.aas_ids.append(aas_id)
        self.result.stats["types"] += 1

    def convert_instance(self, node: dict, path: str) -> None:
        name = node.get("name", "UdtInstance")
        type_path = node.get("typeId", "")
        aas_id = "%s/instance/%s" % (self.ns, sanitize_id_short(path))
        used: set = set()
        # instance parameter overrides / nested content
        elements = [e for e in (self._element(c, used, 1)
                                for c in node.get("tags", [])) if e is not None]
        submodels = []
        if elements:
            sm = model.Submodel(id_=aas_id + "/sm/Content", id_short="Content",
                                semantic_id=ippr_semantic_id("ippr-instance-content"),
                                submodel_element=elements)
            self.result.store.add(sm)
            submodels.append(model.ModelReference.from_referable(sm))
        origin = self._origin_submodel(aas_id + "/sm/IPPROrigin", path,
                                       {"ignitionTagType": "UdtInstance",
                                        "typeId": type_path})
        self.result.store.add(origin)
        submodels.append(model.ModelReference.from_referable(origin))
        aas = model.AssetAdministrationShell(
            id_=aas_id,
            id_short=sanitize_id_short(name),
            display_name=model.MultiLanguageNameType({"en": name[:64]}),
            asset_information=model.AssetInformation(
                asset_kind=model.AssetKind.INSTANCE,
                global_asset_id="%s/asset/instance/%s" % (self.ns, sanitize_id_short(path)),
                asset_type=self._type_ids.get(type_path,
                                              "%s/type/%s" % (self.ns, sanitize_id_short(type_path))),
            ),
            submodel=set(submodels),
        )
        self.result.store.add(aas)
        self.result.aas_ids.append(aas_id)
        self.result.stats["instances"] += 1

    def convert_enterprise(self, root: dict) -> None:
        """The whole tag tree (minus _types_) -> one enterprise AAS with the
        four IPPR branches as submodels."""
        aas_id = "%s/enterprise/%s" % (self.ns, self.company)
        submodels = []
        for child in root.get("tags", []):
            if child.get("name") == "_types_" or child.get("tagType") != "Folder":
                continue
            used: set = set()
            sm = model.Submodel(
                id_="%s/sm/%s" % (aas_id, sanitize_id_short(child["name"])),
                id_short=sanitize_id_short(child["name"]),
                semantic_id=ippr_semantic_id(
                    "ippr-viewpoint-" + sanitize_id_short(child["name"])),
                submodel_element=[
                    e for e in (self._element(c, used, 1)
                                for c in child.get("tags", [])) if e is not None],
            )
            self.result.store.add(sm)
            submodels.append(model.ModelReference.from_referable(sm))
        aas = model.AssetAdministrationShell(
            id_=aas_id,
            id_short="ES_" + self.company,
            display_name=model.MultiLanguageNameType(
                {"en": "Enterprise system %s (IPPR grid)" % self.company}),
            asset_information=model.AssetInformation(
                asset_kind=model.AssetKind.INSTANCE,
                global_asset_id="%s/asset/enterprise/%s" % (self.ns, self.company),
            ),
            submodel=set(submodels),
        )
        self.result.store.add(aas)
        self.result.aas_ids.append(aas_id)


def convert(types_export: str, instances_export: str, namespace: str,
            company: str) -> ConversionResult:
    conv = Udt2AasConverter(namespace, company)
    types_root = load_export(types_export)
    inst_root = load_export(instances_export)

    types_tree = find_subtree(types_root, "_types_") or types_root
    for path, node in iter_nodes(types_tree):
        if node.get("tagType") == "UdtType":
            conv.convert_type(node, path.replace("_types_/", ""))

    for path, node in iter_nodes(inst_root):
        if node.get("tagType") == "UdtInstance" and "_types_" not in path:
            conv.convert_instance(node, path)

    conv.convert_enterprise(inst_root)
    return conv.result


def write_outputs(result: ConversionResult, aasx_path: str, json_path: str) -> None:
    with AASXWriter(aasx_path) as w:
        w.write_aas(result.aas_ids, result.store,
                    DictSupplementaryFileContainer(), write_json=True)
    with open(json_path, "w", encoding="utf-8") as f:
        write_aas_json_file(f, result.store)
