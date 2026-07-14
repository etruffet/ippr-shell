# IPPR Shell

Open-source toolchain for the **Fractal IPPR method** (Eric Truffet):
model an enterprise as a system of systems through one recursive grid —
**I**nformation / **P**roduct / **P**rocess / **R**esources (physical,
digital, functional) — applied at every scale, and keep that model
**executable** on the shop floor.

> *One grid, every scale. — Une seule grille, toutes les échelles.*

## What it does today (v0.1)

```
Ignition UDT/tag JSON export ──udt2aas──▶ AAS package (.aasx, IEC 63278)
                                              │
                          ┌───────────────────┴───────────────────┐
                      aas2ea (Windows + Sparx EA)         aas2xmi (any OS)
                          ▼                                       ▼
          SysML blocks & diagrams              UML2/XMI .uml for Eclipse
          in a native .qea model               Papyrus, Visual Paradigm, …
```

- **udt2aas** — converts Ignition UDT types/instances exports into a conformant
  Asset Administration Shell environment: `UdtType → AAS (kind=Type)`,
  `UdtInstance → AAS (kind=Instance)`, folders → submodels/collections,
  atomic tags → typed properties, full source-path traceability, IPPR
  branch/nature classification.
- **aas2ea** — populates a Sparx Enterprise Architect model (`.qea`) from the
  AASX: one SysML `«block»` per type (with flattened typed attributes), one
  classified object per instance, auto-laid-out overview diagrams. Work on a
  copy of your model so your own profiles are preserved.
- **aas2xmi** — open-tools backend: exports the AASX as an Eclipse UML2 `.uml`
  (XMI) file with the Papyrus SysML 1.6 Blocks profile applied — every system
  is a `«block»`, instances are classified InstanceSpecifications. Also writes
  the Papyrus companion files (`.di` bound to the SysML 1.6 architecture and
  `.notation` with a ready-made "IPPR Blocks" block-definition diagram), so
  the export opens as a full Papyrus model (requires the Papyrus SysML 1.6
  feature); the bare `.uml` still imports into Visual Paradigm or any
  XMI-capable tool. No Windows or EA licence required.

## Quick start

```bash
pip install -e .            # or: pip install -e .[ea]  on Windows for EA export
ippr-shell udt2aas --types export_types.json --instances export_tags.json \
    --namespace https://your.org/ippr --company YourCo --aasx out.aasx
ippr-shell aas2ea --aasx out.aasx --qea copy_of_model.qea --png overview.png
ippr-shell aas2xmi --aasx out.aasx --uml model.uml   # open tools (Papyrus…)
```

## Design principles

1. **The standard is the format, not the tool** — everything flows through
   conformant AASX (IEC 63278 / IDTA specs), built on the official
   [basyx-python-sdk](https://github.com/eclipse-basyx/basyx-python-sdk).
   Conformance is enforced by round-trip tests in CI.
2. **Generate from what already exists** — the number-one obstacle to AAS
   adoption is the manual cost of creating twins; IPPR Shell derives them
   from live SCADA structures.
3. **The model stays fractal** — every resource can itself be a system
   carrying the same IPPR grid, without depth limit.

## Roadmap

- Phase 1 (now): Python converters + conformance CI
- Phase 2: AASX editor with IPPR grid view (fork of the Eclipse AASX
  Package Explorer)
- V2: web-based editor

## License

MIT — course material and models built with this tool are meant to be shared.
