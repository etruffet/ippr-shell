# SPDX-License-Identifier: MIT
"""IPPR Shell command line interface."""
import argparse
import sys


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="ippr-shell",
        description="Fractal IPPR method toolchain: UDT -> AAS -> SysML/EA")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("udt2aas", help="Convert Ignition UDT exports to AASX")
    c.add_argument("--types", required=True, help="UDT types JSON export")
    c.add_argument("--instances", required=True, help="tags/instances JSON export")
    c.add_argument("--namespace", default="https://example.com/ippr")
    c.add_argument("--company", default="Enterprise")
    c.add_argument("--aasx", required=True, help="output .aasx path")
    c.add_argument("--json", default="", help="optional output .json path")

    e = sub.add_parser("aas2ea", help="Populate an EA .qea model from an AASX (Windows + EA required)")
    e.add_argument("--aasx", required=True)
    e.add_argument("--qea", required=True, help="target .qea (work on a copy!)")
    e.add_argument("--png", default="", help="optional diagram PNG export")

    args = p.parse_args(argv)

    if args.cmd == "udt2aas":
        from . import udt2aas
        res = udt2aas.convert(args.types, args.instances, args.namespace, args.company)
        udt2aas.write_outputs(res, args.aasx, args.json or args.aasx + ".json")
        print("AAS: %d  (stats: %s)" % (len(res.aas_ids), res.stats))
    elif args.cmd == "aas2ea":
        from . import aas2ea
        stats = aas2ea.generate(args.aasx, args.qea, args.png)
        print("EA: %s" % stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
