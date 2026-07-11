# SPDX-License-Identifier: MIT
"""Conformance tests for the open-tools backend: AASX -> Eclipse UML2 (.uml)."""
import os
import tempfile
import xml.etree.ElementTree as ET

from ippr_shell import aas2xmi, udt2aas

from test_udt2aas import SYNTH_INSTANCES, SYNTH_TYPES, _write

XMI = aas2xmi.XMI_NS
UML = aas2xmi.UML_NS
BLOCKS = aas2xmi.BLOCKS_NS


def _build_uml(tmp):
    t = _write(tmp, "types.json", SYNTH_TYPES)
    i = _write(tmp, "instances.json", SYNTH_INSTANCES)
    res = udt2aas.convert(t, i, "https://example.com/test", "TestCo")
    aasx = os.path.join(tmp, "out.aasx")
    udt2aas.write_outputs(res, aasx, os.path.join(tmp, "out.json"))
    uml = os.path.join(tmp, "out.uml")
    stats = aas2xmi.generate(aasx, uml)
    return stats, ET.parse(uml).getroot()


def _packages(el):
    return {c.get("name"): c for c in el
            if c.get("{%s}type" % XMI) == "uml:Package"}


def test_generate_structure_and_stereotypes():
    with tempfile.TemporaryDirectory() as tmp:
        stats, root = _build_uml(tmp)

    assert stats["classes"] == 2      # system type + enterprise
    assert stats["instances"] == 1

    assert root.tag == "{%s}XMI" % XMI
    mdl = root.find("{%s}Model" % UML)
    assert mdl is not None

    # Papyrus SysML 1.6 Blocks profile applied on the model
    pa = mdl.find("profileApplication")
    assert pa is not None
    applied = pa.find("appliedProfile")
    assert applied is not None
    assert applied.get("href", "").startswith(aas2xmi.SYSML16)

    # method rule: TYPE / INSTANCE roots mirror Ignition's two trees
    roots = _packages(mdl)
    assert set(roots) >= {"TYPE", "INSTANCE"}

    # TYPE tree: one package per system, holding ONE class (the block)
    sys_pkg = _packages(roots["TYPE"])["RP-01:Press"]
    cls = next(c for c in sys_pkg
               if c.get("{%s}type" % XMI) == "uml:Class")
    assert cls.get("name") == "RP-01:Press"
    cls_id = cls.get("{%s}id" % XMI)

    # flattened typed attributes from the viewpoint submodels
    atts = {a.get("name") for a in cls.findall("ownedAttribute")}
    assert atts == {"Designation", "Running", "Takt_Time"}

    # every class carries the SysML <<Block>> stereotype application
    blocks = root.findall("{%s}Block" % BLOCKS)
    assert {b.get("base_Class") for b in blocks} == {
        c.get("{%s}id" % XMI)
        for c in mdl.iter("packagedElement")
        if c.get("{%s}type" % XMI) == "uml:Class"}

    # INSTANCE tree: instance sits under its tag-path packages,
    # classified by its type's class (fractal link preserved)
    phys = _packages(_packages(roots["INSTANCE"])["RESOURCES"])["PHYSICAL"]
    inst = next(c for c in phys
                if c.get("{%s}type" % XMI) == "uml:InstanceSpecification")
    assert inst.get("name") == "RP-01-PRESS-1"
    assert inst.get("classifier") == cls_id


def test_ids_are_stable():
    with tempfile.TemporaryDirectory() as tmp:
        _, root_a = _build_uml(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        _, root_b = _build_uml(tmp)
    ids_a = [e.get("{%s}id" % XMI) for e in root_a.iter()]
    ids_b = [e.get("{%s}id" % XMI) for e in root_b.iter()]
    assert ids_a == ids_b  # deterministic ids -> diffable, re-importable
