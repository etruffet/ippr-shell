# SPDX-License-Identifier: MIT
"""
aas2ea — Populate a Sparx Enterprise Architect model (.qea) from an AAS
(IEC 63278) package, following the Fractal IPPR method conventions.

Structural rule of the method (Eric Truffet):
    IPPR viewpoint  = Ignition folder  = SysML PACKAGE (same name)
    sub-folders     = sub-packages (structure is never flattened into names)
    the system      = ONE SysML block, named after the system (= the UDT)

Mapping applied here:
    AAS kind=TYPE      -> package <System> { block <System> «block»
                           + viewpoint package tree mirroring its submodels }
    AAS kind=INSTANCE  -> EA Object named after the instance, classified by
                          its type block, placed in the package mirroring its
                          original tag path
    Enterprise AAS     -> block ES_<Company> + the four IPPR viewpoint
                          packages mirroring the enterprise tag tree
    Atomic properties  -> typed attributes on the system block, plain names,
                          full source path kept in the attribute Notes
"""
from __future__ import annotations

import datetime

import win32com.client

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

GRID_COLS = 4
CELL_W, CELL_H, GAP_X, GAP_Y = 230, 150, 45, 60
ENTERPRISE_PKG_DEPTH = 3   # mirror enterprise folders as packages up to this depth


def _vt_name(value_type) -> str:
    n = getattr(value_type, "__name__", str(value_type))
    return {"Int": "Integer", "Long": "Integer", "Double": "Real",
            "Float": "Real", "Boolean": "Boolean", "String": "String"}.get(n, "String")


def _dname(obj, fallback: str = "") -> str:
    """Best human name: display_name (original Ignition name) else id_short."""
    dn = getattr(obj, "display_name", None)
    if dn:
        try:
            for lang in ("en", "fr"):
                if lang in dn:
                    return str(dn[lang])
            return str(next(iter(dn.values())))
        except Exception:
            pass
    return getattr(obj, "id_short", None) or fallback


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
        pkg = parent.Packages.AddNew(name[:200], "")
        pkg.Update()
        return pkg

    def add_block(self, pkg, name: str, notes: str, attrs) -> object:
        """attrs: iterable of (name, type, note)."""
        el = pkg.Elements.AddNew(name[:200], "Class")
        el.StereotypeEx = "SysML1.4::block"
        el.Notes = notes
        el.Update()
        used = set()
        for aname, atype, anote in attrs:
            base = aname[:200]
            final = base
            i = 1
            while final in used:
                i += 1
                final = "%s_%d" % (base[:190], i)
            used.add(final)
            a = el.Attributes.AddNew(final, atype)
            a.Notes = anote
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


def _collect(elements, path=""):
    """Yield ('pkg'|'prop', path, element) walking submodel content."""
    for el in elements:
        if isinstance(el, model.Property):
            yield "prop", path, el
        elif isinstance(el, model.SubmodelElementCollection):
            here = "%s/%s" % (path, _dname(el)) if path else _dname(el)
            yield "pkg", here, el
            yield from _collect(el.value, here)
        elif isinstance(el, model.ReferenceElement):
            yield "ref", path, el


def _build_viewpoint_tree(ea, parent_pkg, submodels, max_depth=99):
    """Create the viewpoint package tree; return list of (attr_name, type, note)."""
    attrs = []
    for sm in submodels:
        vp_pkg = ea.sub_package(parent_pkg, _dname(sm))
        pkg_map = {"": vp_pkg}
        for kind, path, el in _collect(sm.submodel_element):
            depth = path.count("/") + 1 if path else 0
            if kind == "pkg" and depth <= max_depth:
                parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
                parent = pkg_map.get(parent_path, vp_pkg)
                pkg_map[path] = ea.sub_package(parent, path.rsplit("/", 1)[-1])
            elif kind == "prop":
                note = "%s/%s" % (_dname(sm), path) if path else _dname(sm)
                attrs.append((_dname(el), _vt_name(el.value_type), note))
    return attrs


def generate(aasx_path: str, qea_path: str, png_path: str = "") -> dict:
    store = load_aasx(aasx_path)
    shells = [o for o in store if isinstance(o, model.AssetAdministrationShell)]
    types = [a for a in shells if a.asset_information.asset_kind == model.AssetKind.TYPE]
    insts = [a for a in shells
             if a.asset_information.asset_kind == model.AssetKind.INSTANCE
             and not (a.id_short or "").startswith("ES_")]
    enterprise = next((a for a in shells if (a.id_short or "").startswith("ES_")), None)

    ea = EaWriter(qea_path)
    stats = {"blocks": 0, "instances": 0, "packages": 0}
    try:
        stamp = datetime.date.today().isoformat()
        root = ea.root_package("GENERATED FROM UDT (IPPR Shell, %s)" % stamp)

        # ------ enterprise system: one block + IPPR viewpoint packages ------
        ent_pkg_map = {}
        if enterprise is not None:
            ent_sms = [
                _resolve(store, r) for r in sorted(enterprise.submodel, key=str)]
            ent_sms = [s for s in ent_sms if s is not None and s.id_short != "IPPROrigin"]
            ent_attrs = []
            for sm in ent_sms:
                vp_pkg = ea.sub_package(root, _dname(sm))
                stats["packages"] += 1
                ent_pkg_map[_dname(sm)] = vp_pkg
                pkg_map = {"": vp_pkg}
                for kind, path, el in _collect(sm.submodel_element):
                    depth = path.count("/") + 1 if path else 0
                    if kind == "pkg" and depth <= ENTERPRISE_PKG_DEPTH:
                        parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
                        parent = pkg_map.get(parent_path, vp_pkg)
                        p = ea.sub_package(parent, path.rsplit("/", 1)[-1])
                        pkg_map[path] = p
                        ent_pkg_map["%s/%s" % (_dname(sm), path)] = p
                        stats["packages"] += 1
                    elif kind == "prop":
                        note = "%s/%s" % (_dname(sm), path) if path else _dname(sm)
                        ent_attrs.append((_dname(el), _vt_name(el.value_type), note))
            ea.add_block(root, _dname(enterprise),
                         "Enterprise system (IPPR grid).\nAAS id: %s" % enterprise.id,
                         ent_attrs)
            stats["blocks"] += 1

        # ------ types: package per system { block + viewpoint packages } ----
        p_types = ea.sub_package(root, "_TYPES (UDT definitions)")
        type_elements = {}
        for aas in sorted(types, key=lambda a: _dname(a)):
            origin = _origin_info(store, aas)
            sys_pkg = ea.sub_package(p_types, _dname(aas))
            stats["packages"] += 1
            sms = [_resolve(store, r) for r in sorted(aas.submodel, key=str)]
            sms = [s for s in sms if s is not None and s.id_short != "IPPROrigin"]
            attrs = _build_viewpoint_tree(ea, sys_pkg, sms)
            notes = "System block (Fractal IPPR).\nAAS id: %s\nSource: %s" % (
                aas.id, origin.get("sourcePath", "?"))
            el = ea.add_block(sys_pkg, _dname(aas), notes, attrs)
            type_elements[aas.id] = el
            stats["blocks"] += 1

        # ------ instances: Object in the package mirroring its tag path -----
        inst_elements = []
        for aas in sorted(insts, key=lambda a: _dname(a)):
            origin = _origin_info(store, aas)
            src = origin.get("sourcePath", "")
            parent_path = src.rsplit("/", 1)[0] if "/" in src else ""
            pkg = ent_pkg_map.get(parent_path)
            if pkg is None:  # create missing chain (shallow packages only)
                pkg = root
                walked = []
                for seg in parent_path.split("/"):
                    walked.append(seg)
                    key = "/".join(walked)
                    if key not in ent_pkg_map:
                        ent_pkg_map[key] = ea.sub_package(pkg, seg)
                        stats["packages"] += 1
                    pkg = ent_pkg_map[key]
            type_id = aas.asset_information.asset_type
            cls = type_elements.get(type_id)
            notes = "AAS id: %s\nSource: %s\nType: %s" % (aas.id, src, type_id or "?")
            el = ea.add_instance(pkg, _dname(aas), notes,
                                 cls.ElementID if cls is not None else 0)
            inst_elements.append(el)
            stats["instances"] += 1

        dia = ea.add_diagram(p_types, "bdd TYPES overview",
                             list(type_elements.values()))
        ea.add_diagram(root, "Instances overview", inst_elements)
        if png_path:
            ea.export_png(dia, png_path)
    finally:
        ea.close()
    return stats
