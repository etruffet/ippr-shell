# SPDX-License-Identifier: MIT
"""
aas2xmi — Export an AAS (IEC 63278) package as an Eclipse UML2 model file
(.uml, XMI flavour) that opens natively in Eclipse Papyrus — and imports
into Visual Paradigm or any XMI-capable UML tool.

Open-tools backend of IPPR Shell: same structural rule as aas2ea —
    IPPR viewpoint = PACKAGE, sub-folders = sub-packages,
    the system = ONE class (block), named after the system,
    instances = InstanceSpecifications classified by their type.

Diagrams are not part of XMI; arrange elements in Papyrus (or let the
tool auto-arrange). Stereotype application (SysML «block») is left to the
target tool profile; the source path of every element is kept in comments.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

XMI_NS = "http://www.omg.org/spec/XMI/20131001"
UML_NS = "http://www.eclipse.org/uml2/5.0.0/UML"
PRIMS = "pathmap://UML_LIBRARIES/UMLPrimitiveTypes.library.uml#"

ET.register_namespace("xmi", XMI_NS)
ET.register_namespace("uml", UML_NS)


def _vt_name(value_type) -> str:
    n = getattr(value_type, "__name__", str(value_type))
    return {"Int": "Integer", "Long": "Integer", "Double": "Real",
            "Float": "Real", "Boolean": "Boolean", "String": "String"}.get(n, "String")


def _dname(obj, fallback: str = "") -> str:
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


def _xid(*parts) -> str:
    return "_" + hashlib.md5("/".join(str(p) for p in parts).encode()).hexdigest()[:20]


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


class UmlBuilder:
    def __init__(self, name: str):
        self.root = ET.Element("{%s}Model" % UML_NS,
                               {"{%s}version" % XMI_NS: "20131001",
                                "{%s}id" % XMI_NS: _xid("model", name),
                                "name": name})

    def package(self, parent, name, key):
        el = ET.SubElement(parent, "packagedElement",
                           {"{%s}type" % XMI_NS: "uml:Package",
                            "{%s}id" % XMI_NS: _xid("pkg", key),
                            "name": name})
        return el

    def clazz(self, parent, name, key, comment=""):
        el = ET.SubElement(parent, "packagedElement",
                           {"{%s}type" % XMI_NS: "uml:Class",
                            "{%s}id" % XMI_NS: _xid("cls", key),
                            "name": name})
        if comment:
            self.comment(el, comment, key)
        return el

    def attribute(self, cls_el, name, vtype, key, comment=""):
        a = ET.SubElement(cls_el, "ownedAttribute",
                          {"{%s}id" % XMI_NS: _xid("att", key),
                           "name": name})
        ET.SubElement(a, "type", {"{%s}type" % XMI_NS: "uml:PrimitiveType",
                                  "href": PRIMS + vtype})
        if comment:
            self.comment(a, comment, key + "/c")
        return a

    def instance(self, parent, name, key, classifier_id="", comment=""):
        attrs = {"{%s}type" % XMI_NS: "uml:InstanceSpecification",
                 "{%s}id" % XMI_NS: _xid("ins", key),
                 "name": name}
        if classifier_id:
            attrs["classifier"] = classifier_id
        el = ET.SubElement(parent, "packagedElement", attrs)
        if comment:
            self.comment(el, comment, key)
        return el

    def comment(self, owner, body, key):
        c = ET.SubElement(owner, "ownedComment",
                          {"{%s}id" % XMI_NS: _xid("com", key)})
        b = ET.SubElement(c, "body")
        b.text = body

    def write(self, path):
        ET.indent(self.root)
        ET.ElementTree(self.root).write(path, encoding="UTF-8",
                                        xml_declaration=True)


def _walk_content(builder, parent_el, elements, base_key, cls_el, sm_name,
                  path="", depth=0):
    """SMC -> sub-package; Property -> attribute on the system class."""
    for el in elements:
        if isinstance(el, model.SubmodelElementCollection):
            here = "%s/%s" % (path, _dname(el)) if path else _dname(el)
            pkg = builder.package(parent_el, _dname(el), base_key + "/" + here)
            _walk_content(builder, pkg, el.value, base_key, cls_el, sm_name,
                          here, depth + 1)
        elif isinstance(el, model.Property) and cls_el is not None:
            note = "%s/%s" % (sm_name, path) if path else sm_name
            builder.attribute(cls_el, _dname(el), _vt_name(el.value_type),
                              base_key + "/" + note + "/" + _dname(el), note)


def generate(aasx_path: str, uml_path: str) -> dict:
    store = load_aasx(aasx_path)
    shells = [o for o in store if isinstance(o, model.AssetAdministrationShell)]
    types = [a for a in shells if a.asset_information.asset_kind == model.AssetKind.TYPE]
    insts = [a for a in shells
             if a.asset_information.asset_kind == model.AssetKind.INSTANCE
             and not (a.id_short or "").startswith("ES_")]
    enterprise = next((a for a in shells if (a.id_short or "").startswith("ES_")), None)

    b = UmlBuilder("GENERATED FROM UDT (IPPR Shell)")
    stats = {"classes": 0, "instances": 0, "packages": 0}

    # enterprise viewpoint packages + enterprise class
    ent_pkgs = {}
    if enterprise is not None:
        ent_cls = b.clazz(b.root, _dname(enterprise), enterprise.id,
                          "Enterprise system (IPPR grid). AAS id: " + enterprise.id)
        stats["classes"] += 1
        for ref in sorted(enterprise.submodel, key=str):
            sm = _resolve(store, ref)
            if sm is None or sm.id_short == "IPPROrigin":
                continue
            vp = b.package(b.root, _dname(sm), enterprise.id + "/" + _dname(sm))
            ent_pkgs[_dname(sm)] = vp
            stats["packages"] += 1
            _walk_content(b, vp, sm.submodel_element, enterprise.id, ent_cls,
                          _dname(sm))

    # types: package per system { class + viewpoint packages }
    p_types = b.package(b.root, "_TYPES (UDT definitions)", "_types")
    class_ids = {}
    for aas in sorted(types, key=lambda a: _dname(a)):
        origin = _origin_info(store, aas)
        sys_pkg = b.package(p_types, _dname(aas), aas.id)
        stats["packages"] += 1
        cls = b.clazz(sys_pkg, _dname(aas), aas.id,
                      "System block (Fractal IPPR). AAS id: %s | Source: %s"
                      % (aas.id, origin.get("sourcePath", "?")))
        class_ids[aas.id] = cls.get("{%s}id" % XMI_NS)
        stats["classes"] += 1
        for ref in sorted(aas.submodel, key=str):
            sm = _resolve(store, ref)
            if sm is None or sm.id_short == "IPPROrigin":
                continue
            vp = b.package(sys_pkg, _dname(sm), aas.id + "/" + _dname(sm))
            _walk_content(b, vp, sm.submodel_element, aas.id, cls, _dname(sm))

    # instances placed in packages mirroring their tag path
    for aas in sorted(insts, key=lambda a: _dname(a)):
        origin = _origin_info(store, aas)
        src = origin.get("sourcePath", "")
        parent_path = src.rsplit("/", 1)[0] if "/" in src else ""
        # walk/create package chain under the matching enterprise viewpoint
        segs = parent_path.split("/") if parent_path else []
        pkg = ent_pkgs.get(segs[0]) if segs else None
        if pkg is None:
            pkg = b.root
            segs = segs or [""]
        node, key = pkg, segs[0] if segs else ""
        for seg in segs[1:]:
            key = key + "/" + seg
            found = None
            for child in node:
                if child.get("name") == seg and child.get(
                        "{%s}type" % XMI_NS) == "uml:Package":
                    found = child
                    break
            node = found if found is not None else b.package(node, seg, "ep/" + key)
        b.instance(node, _dname(aas), aas.id,
                   class_ids.get(aas.asset_information.asset_type, ""),
                   "AAS id: %s | Source: %s" % (aas.id, src))
        stats["instances"] += 1

    b.write(uml_path)
    return stats
