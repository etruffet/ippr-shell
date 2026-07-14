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
tool auto-arrange). The Papyrus SysML 1.6 Blocks profile is applied and every
system class carries the SysML «Block» stereotype; the source path of every
element is kept in comments.
"""
from __future__ import annotations

import hashlib
import os
import xml.etree.ElementTree as ET

from basyx.aas import model
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer

XMI_NS = "http://www.omg.org/spec/XMI/20131001"
UML_NS = "http://www.eclipse.org/uml2/5.0.0/UML"
ECORE_NS = "http://www.eclipse.org/emf/2002/Ecore"
BLOCKS_NS = "http://www.eclipse.org/papyrus/sysml/1.6/SysML/Blocks"
PRIMS = "pathmap://UML_LIBRARIES/UMLPrimitiveTypes.library.uml#"
# Papyrus SysML 1.6 profile. The profile is statically defined: stereotype
# EClasses live in registered EPackages (plugin.xml generated_package), so the
# profileApplication must reference the Blocks nsURI root ("<nsURI>#/"), not an
# id inside SysML.profile.uml.
SYSML16 = "pathmap://SysML16_PROFILES/SysML.profile.uml"
BLOCKS_PROFILE_ID = "SysML.package_packagedElement_Blocks"

# Papyrus companion files (.di/.notation) use XMI 2.0, not 20131001, and the
# style/uml prefixes only occur inside attribute values — so both files are
# built with literal (non-Clark) tag/attribute names and explicit xmlns attrs.
XMI2_NS = "http://www.omg.org/XMI"
ARCH_NS = "http://www.eclipse.org/papyrus/infra/core/architecture"
NOTATION_NS = "http://www.eclipse.org/gmf/runtime/1.0.2/notation"
STYLE_NS = "http://www.eclipse.org/papyrus/infra/gmfdiag/style"
SYSML16_CONTEXT = "org.eclipse.papyrus.sysml.architecture.SysML16"
BDD_KIND = "org.eclipse.papyrus.sysml16.diagram.blockdefinition"

ET.register_namespace("xmi", XMI_NS)
ET.register_namespace("uml", UML_NS)
ET.register_namespace("ecore", ECORE_NS)
ET.register_namespace("Blocks", BLOCKS_NS)


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
        self.xmi = ET.Element("{%s}XMI" % XMI_NS,
                              {"{%s}version" % XMI_NS: "20131001"})
        # "ecore" only occurs inside attribute values (xmi:type="ecore:EPackage"),
        # so ElementTree would never emit its declaration — force it or EMF
        # fails with ClassNotFoundException: 'EPackage'.
        self.xmi.set("xmlns:ecore", ECORE_NS)
        self.root = ET.SubElement(self.xmi, "{%s}Model" % UML_NS,
                                  {"{%s}id" % XMI_NS: _xid("model", name),
                                   "name": name})
        # apply the Papyrus SysML 1.6 Blocks profile so «Block» resolves
        pa = ET.SubElement(self.root, "profileApplication",
                           {"{%s}id" % XMI_NS: _xid("pa", name)})
        ann = ET.SubElement(pa, "eAnnotations",
                            {"{%s}id" % XMI_NS: _xid("ann", name),
                             "source": "http://www.eclipse.org/uml2/2.0.0/UML"})
        ET.SubElement(ann, "references",
                      {"{%s}type" % XMI_NS: "ecore:EPackage",
                       "href": BLOCKS_NS + "#/"})
        ET.SubElement(pa, "appliedProfile",
                      {"href": "%s#%s" % (SYSML16, BLOCKS_PROFILE_ID)})

    def apply_block(self, cls_el):
        """SysML «Block» stereotype application (Papyrus SysML 1.6)."""
        cid = cls_el.get("{%s}id" % XMI_NS)
        ET.SubElement(self.xmi, "{%s}Block" % BLOCKS_NS,
                      {"{%s}id" % XMI_NS: _xid("stblock", cid),
                       "base_Class": cid})

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
        ET.indent(self.xmi)
        ET.ElementTree(self.xmi).write(path, encoding="UTF-8",
                                       xml_declaration=True)


def _write_di(path: str):
    """Papyrus entry point: binds the model to the SysML 1.6 architecture."""
    di = ET.Element("architecture:ArchitectureDescription",
                    {"xmi:version": "2.0",
                     "xmlns:xmi": XMI2_NS,
                     "xmlns:architecture": ARCH_NS,
                     "contextId": SYSML16_CONTEXT})
    ET.ElementTree(di).write(path, encoding="UTF-8", xml_declaration=True)


def _write_notation(path: str, uml_file: str, model_id: str, blocks):
    """One SysML BDD ("IPPR Blocks") showing every «Block» class on a grid."""
    xmi = ET.Element("xmi:XMI", {"xmi:version": "2.0",
                                 "xmlns:xmi": XMI2_NS,
                                 "xmlns:notation": NOTATION_NS,
                                 "xmlns:style": STYLE_NS,
                                 "xmlns:uml": UML_NS})
    dg = ET.SubElement(xmi, "notation:Diagram",
                       {"xmi:id": _xid("bdd", uml_file),
                        "type": "PapyrusUMLClassDiagram",
                        "name": "IPPR Blocks", "measurementUnit": "Pixel"})
    for i, (cid, _name) in enumerate(blocks):
        shape = ET.SubElement(dg, "children",
                              {"xmi:type": "notation:Shape",
                               "xmi:id": _xid("shape", cid),
                               "type": "Class_Shape"})
        ET.SubElement(shape, "children",
                      {"xmi:type": "notation:DecorationNode",
                       "xmi:id": _xid("shname", cid),
                       "type": "Class_NameLabel"})
        fl = ET.SubElement(shape, "children",
                           {"xmi:type": "notation:DecorationNode",
                            "xmi:id": _xid("shfloat", cid),
                            "type": "Class_FloatingNameLabel"})
        ET.SubElement(fl, "layoutConstraint",
                      {"xmi:type": "notation:Location",
                       "xmi:id": _xid("shfloatloc", cid), "y": "15"})
        for comp in ("Class_AttributeCompartment", "Class_OperationCompartment",
                     "Class_NestedClassifierCompartment"):
            c = ET.SubElement(shape, "children",
                              {"xmi:type": "notation:BasicCompartment",
                               "xmi:id": _xid("cmp", cid, comp),
                               "type": comp})
            for st in ("TitleStyle", "SortingStyle", "FilteringStyle"):
                ET.SubElement(c, "styles",
                              {"xmi:type": "notation:" + st,
                               "xmi:id": _xid("cmps", cid, comp, st)})
            ET.SubElement(c, "layoutConstraint",
                          {"xmi:type": "notation:Bounds",
                           "xmi:id": _xid("cmpb", cid, comp)})
        ET.SubElement(shape, "element", {"xmi:type": "uml:Class",
                                         "href": "%s#%s" % (uml_file, cid)})
        ET.SubElement(shape, "layoutConstraint",
                      {"xmi:type": "notation:Bounds",
                       "xmi:id": _xid("shb", cid),
                       "x": str(40 + (i % 3) * 280),
                       "y": str(40 + (i // 3) * 200),
                       "width": "240", "height": "140"})
    ET.SubElement(dg, "styles", {"xmi:type": "notation:StringValueStyle",
                                 "xmi:id": _xid("dgv", uml_file),
                                 "name": "diagram_compatibility_version",
                                 "stringValue": "1.3.0"})
    ET.SubElement(dg, "styles", {"xmi:type": "notation:DiagramStyle",
                                 "xmi:id": _xid("dgs", uml_file)})
    pds = ET.SubElement(dg, "styles",
                        {"xmi:type": "style:PapyrusDiagramStyle",
                         "xmi:id": _xid("dgp", uml_file),
                         "diagramKindId": BDD_KIND})
    ET.SubElement(pds, "owner", {"xmi:type": "uml:Model",
                                 "href": "%s#%s" % (uml_file, model_id)})
    ET.SubElement(dg, "element", {"xmi:type": "uml:Model",
                                  "href": "%s#%s" % (uml_file, model_id)})
    ET.indent(xmi)
    ET.ElementTree(xmi).write(path, encoding="UTF-8", xml_declaration=True)


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
    stats = {"classes": 0, "instances": 0, "packages": 0, "diagrams": 0}
    blocks = []  # (class xmi:id, name) — shown on the generated BDD

    # Standardized roots mirroring Ignition's two trees (method rule):
    # TYPE = UDT treeview (types only), INSTANCE = Tags tree (instances only)
    p_instance = b.package(b.root, "INSTANCE", "_instance_root")

    # enterprise viewpoint packages + enterprise class
    ent_pkgs = {}
    if enterprise is not None:
        ent_cls = b.clazz(p_instance, _dname(enterprise), enterprise.id,
                          "Enterprise system (IPPR grid). AAS id: " + enterprise.id)
        b.apply_block(ent_cls)
        blocks.append((ent_cls.get("{%s}id" % XMI_NS), _dname(enterprise)))
        stats["classes"] += 1
        for ref in sorted(enterprise.submodel, key=str):
            sm = _resolve(store, ref)
            if sm is None or sm.id_short == "IPPROrigin":
                continue
            vp = b.package(p_instance, _dname(sm), enterprise.id + "/" + _dname(sm))
            ent_pkgs[_dname(sm)] = vp
            stats["packages"] += 1
            _walk_content(b, vp, sm.submodel_element, enterprise.id, ent_cls,
                          _dname(sm))

    # types: package per system { class + viewpoint packages }
    p_types = b.package(b.root, "TYPE", "_types")
    class_ids = {}
    for aas in sorted(types, key=lambda a: _dname(a)):
        origin = _origin_info(store, aas)
        sys_pkg = b.package(p_types, _dname(aas), aas.id)
        stats["packages"] += 1
        cls = b.clazz(sys_pkg, _dname(aas), aas.id,
                      "System block (Fractal IPPR). AAS id: %s | Source: %s"
                      % (aas.id, origin.get("sourcePath", "?")))
        class_ids[aas.id] = cls.get("{%s}id" % XMI_NS)
        b.apply_block(cls)
        blocks.append((class_ids[aas.id], _dname(aas)))
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
            pkg = p_instance
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

    # Papyrus companions: .di (architecture context) + .notation (one BDD),
    # so the export opens as a full Papyrus model, not a bare UML file
    base = uml_path[:-4] if uml_path.lower().endswith(".uml") else uml_path
    _write_di(base + ".di")
    _write_notation(base + ".notation", os.path.basename(uml_path),
                    b.root.get("{%s}id" % XMI_NS), blocks)
    stats["diagrams"] = 1
    return stats
