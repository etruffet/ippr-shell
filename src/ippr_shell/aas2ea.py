# SPDX-License-Identifier: MIT
"""
aas2ea — Populate a Sparx Enterprise Architect model (.qea) from an AAS
(IEC 63278) package, following the Fractal IPPR method conventions.

Mapping:
    AAS assetKind=TYPE      -> SysML block  (Class, stereotype SysML1.4::block)
    AAS assetKind=INSTANCE  -> EA Object classified by its type block
    Submodel / SMC          -> block attributes (flattened, prefixed)
    IPPROrigin.sourcePath   -> element Notes (traceability to Ignition)

The target .qea should be a copy of the user's model so that his IPPR
profile, viewpoints and existing packages are preserved; generated content
is isolated under one root package.
"""
from __future__ import annotations

import datetime

import win32com.client

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

GRID_COLS = 4
CELL_W, CELL_H, GAP_X, GAP_Y = 220, 130, 45, 55


def _vt_name(value_type) -> str:
    n = getattr(value_type, "__name__", str(value_type))
    return {"Int": "Integer", "Long": "Integer", "Double": "Real",
            "Float": "Real", "Boolean": "Boolean", "String": "String"}.get(n, "String")


def load_aasx(path: str):
    store = model.DictObjectStore()
    with AASXReader(path) as r:
        r.read_into(store, DictSupplementaryFileContainer())
    return store


def _resolve(store, ref):
    try:
        return ref.resolve(store)
    except Exception:
        return None


def _origin_info(store, aas) -> dict:
    info = {}
    for ref in aas.submodel:
        sm = _resolve(store, ref)
        if sm is not None and sm.id_short == "IPPROrigin":
            for el in sm.submodel_element:
                if isinstance(el, model.Property):
                    info[el.id_short] = el.value
    return info


def _flatten_attrs(store, aas, max_attrs: int = 60):
    """Yield (name, type) attribute candidates from the AAS submodels."""
    count = 0
    for ref in sorted(aas.submodel, key=lambda r: str(r)):
        sm = _resolve(store, ref)
        if sm is None or sm.id_short == "IPPROrigin":
            continue
        prefix = (sm.id_short or "SM")[:12]
        for name, vt in _walk_props(sm.submodel_element, prefix, depth=0):
            yield name, vt
            count += 1
            if count >= max_attrs:
                return


def _walk_props(elements, prefix, depth):
    if depth > 2:
        return
    for el in elements:
        if isinstance(el, model.Property):
            yield ("%s.%s" % (prefix, el.id_short))[:250], _vt_name(el.value_type)
        elif isinstance(el, model.SubmodelElementCollection):
            yield from _walk_props(el.value, "%s.%s" % (prefix, el.id_short[:16]), depth + 1)


class EaWriter:
    def __init__(self, qea_path: str):
        self.rep = win32com.client.Dispatch("EA.Repository")
        if not self.rep.OpenFile(qea_path):
            raise RuntimeError("EA could not open " + qea_path)

    def close(self):
        self.rep.Exit()

    def root_package(self, name: str):
        root = self.rep.Models.GetAt(0)
        pkg = root.Packages.AddNew(name, "")
        pkg.Update()
        return pkg

    def sub_package(self, parent, name: str):
        pkg = parent.Packages.AddNew(name, "")
        pkg.Update()
        return pkg

    def add_block(self, pkg, name: str, notes: str, attrs) -> object:
        el = pkg.Elements.AddNew(name[:200], "Class")
        el.StereotypeEx = "SysML1.4::block"
        el.Notes = notes
        el.Update()
        for aname, atype in attrs:
            a = el.Attributes.AddNew(aname, atype)
            a.Update()
        return el

    def add_instance(self, pkg, name: str, notes: str, classifier_id: int) -> object:
        el = pkg.Elements.AddNew(name[:200], "Object")
        if classifier_id:
            el.ClassifierID = classifier_id
        el.Notes = notes
        el.Update()
        return el

    def add_diagram(self, pkg, name: str, elements) -> object:
        dia = pkg.Diagrams.AddNew(name, "Logical")
        dia.Update()
        for i, el in enumerate(elements):
            col, row = i % GRID_COLS, i // GRID_COLS
            left = 40 + col * (CELL_W + GAP_X)
            top = 40 + row * (CELL_H + GAP_Y)
            pos = "l=%d;r=%d;t=-%d;b=-%d;" % (left, left + CELL_W, top, top + CELL_H)
            dob = dia.DiagramObjects.AddNew(pos, "")
            dob.ElementID = el.ElementID
            dob.Update()
        return dia

    def export_png(self, diagram, path: str):
        proj = self.rep.GetProjectInterface()
        proj.PutDiagramImageToFile(diagram.DiagramGUID, path, 1)


def generate(aasx_path: str, qea_path: str, png_path: str = "") -> dict:
    store = load_aasx(aasx_path)
    shells = [o for o in store if isinstance(o, model.AssetAdministrationShell)]
    types = [a for a in shells if a.asset_information.asset_kind == model.AssetKind.TYPE]
    insts = [a for a in shells
             if a.asset_information.asset_kind == model.AssetKind.INSTANCE]

    ea = EaWriter(qea_path)
    stats = {"blocks": 0, "instances": 0}
    try:
        stamp = datetime.date.today().isoformat()
        root = ea.root_package("GENERATED FROM UDT (IPPR Shell, %s)" % stamp)
        p_types = ea.sub_package(root, "1 TYPES (UDT -> blocks)")
        p_insts = ea.sub_package(root, "2 INSTANCES")

        type_elements = {}
        for aas in sorted(types, key=lambda a: a.id_short or ""):
            origin = _origin_info(store, aas)
            notes = "Generated by IPPR Shell udt2aas/aas2ea.\nAAS id: %s\nSource: %s" % (
                aas.id, origin.get("sourcePath", "?"))
            el = ea.add_block(p_types, aas.id_short or "block",
                              notes, _flatten_attrs(store, aas))
            type_elements[aas.id] = el
            stats["blocks"] += 1

        inst_elements = []
        for aas in sorted(insts, key=lambda a: a.id_short or ""):
            if (aas.id_short or "").startswith("ES_"):
                continue  # enterprise AAS handled as context, not instance
            origin = _origin_info(store, aas)
            type_id = aas.asset_information.asset_type
            cls = type_elements.get(type_id)
            notes = "AAS id: %s\nSource: %s\nType: %s" % (
                aas.id, origin.get("sourcePath", "?"), type_id or "?")
            el = ea.add_instance(p_insts, aas.id_short or "instance", notes,
                                 cls.ElementID if cls is not None else 0)
            inst_elements.append(el)
            stats["instances"] += 1

        dia_t = ea.add_diagram(p_types, "bdd TYPES overview",
                               list(type_elements.values()))
        ea.add_diagram(p_insts, "INSTANCES overview", inst_elements)
        if png_path:
            ea.export_png(dia_t, png_path)
    finally:
        ea.close()
    return stats
