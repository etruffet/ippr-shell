# SPDX-License-Identifier: MIT
"""
IPPR Shell — graphical launcher (tkinter, stdlib only).

A lightweight GUI over the converter chain:
    1. Ignition UDT exports  ->  AASX (IEC 63278)
    2. AASX                  ->  Sparx EA model (.qea, SysML blocks)

French-first UI (primary users: the method author and his students).
"""
from __future__ import annotations

import json
import os
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, ttk

SETTINGS = os.path.join(os.path.expanduser("~"), ".ippr_shell_gui.json")


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        master.title("IPPR Shell — méthode IPPR Fractale")
        master.minsize(720, 560)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        self.vars = {k: tk.StringVar() for k in
                     ("types", "instances", "namespace", "company",
                      "aasx", "qea", "png")}
        self.vars["namespace"].set("https://example.com/ippr")
        self.vars["company"].set("Enterprise")
        self._load_settings()

        r = 0
        r = self._section(r, "1 · UDT Ignition  →  AASX (IEC 63278)")
        r = self._file_row(r, "Export types UDT (.json)", "types",
                           [("JSON", "*.json")])
        r = self._file_row(r, "Export tags/instances (.json)", "instances",
                           [("JSON", "*.json")])
        r = self._entry_row(r, "Namespace (URI)", "namespace")
        r = self._entry_row(r, "Entreprise", "company")
        r = self._file_row(r, "Sortie AASX (.aasx)", "aasx",
                           [("AASX", "*.aasx")], save=True)
        btn1 = ttk.Button(self, text="Convertir  UDT → AASX",
                          command=lambda: self._run(self.run_udt2aas))
        btn1.grid(row=r, column=1, sticky="w", pady=(2, 10)); r += 1

        r = self._section(r, "2 · AASX  →  Enterprise Architect (.qea)")
        r = self._file_row(r, "Modèle EA cible (.qea) — copie !", "qea",
                           [("EA model", "*.qea")])
        r = self._file_row(r, "Aperçu diagramme (.png, optionnel)", "png",
                           [("PNG", "*.png")], save=True)
        btn2 = ttk.Button(self, text="Générer le modèle EA",
                          command=lambda: self._run(self.run_aas2ea))
        btn2.grid(row=r, column=1, sticky="w", pady=(2, 10)); r += 1

        chain = ttk.Button(self, text="▶  Chaîne complète  UDT → AASX → EA",
                           command=lambda: self._run(self.run_chain))
        chain.grid(row=r, column=0, columnspan=3, pady=(0, 8)); r += 1

        self.log = tk.Text(self, height=12, state="disabled",
                           font=("Consolas", 9))
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(r, weight=1)
        self._say("Prêt. Une seule grille, toutes les échelles.")

    # -- UI helpers ---------------------------------------------------------

    def _section(self, r, text):
        lbl = ttk.Label(self, text=text, font=("Segoe UI", 11, "bold"))
        lbl.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 4))
        return r + 1

    def _entry_row(self, r, label, key):
        ttk.Label(self, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(self, textvariable=self.vars[key]).grid(
            row=r, column=1, columnspan=2, sticky="ew", pady=1)
        return r + 1

    def _file_row(self, r, label, key, types, save=False):
        ttk.Label(self, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(self, textvariable=self.vars[key]).grid(
            row=r, column=1, sticky="ew", pady=1)

        def pick():
            fn = (filedialog.asksaveasfilename(filetypes=types,
                                               defaultextension=types[0][1][1:])
                  if save else filedialog.askopenfilename(filetypes=types))
            if fn:
                self.vars[key].set(fn)
        ttk.Button(self, text="…", width=3, command=pick).grid(
            row=r, column=2, padx=(4, 0))
        return r + 1

    def _say(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _run(self, fn):
        self._save_settings()

        def task():
            try:
                fn()
            except Exception:
                self._say("ERREUR :\n" + traceback.format_exc())
        threading.Thread(target=task, daemon=True).start()

    # -- actions ------------------------------------------------------------

    def run_udt2aas(self):
        from . import udt2aas
        v = {k: s.get().strip() for k, s in self.vars.items()}
        if not (v["types"] and v["instances"] and v["aasx"]):
            self._say("⚠ Renseigner les deux exports et la sortie AASX.")
            return
        self._say("Conversion UDT → AASX…")
        res = udt2aas.convert(v["types"], v["instances"],
                              v["namespace"], v["company"])
        udt2aas.write_outputs(res, v["aasx"], v["aasx"] + ".json")
        self._say("✔ %d AAS écrits (%s)" % (len(res.aas_ids), res.stats))
        self._say("   → " + v["aasx"])

    def run_aas2ea(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            from . import aas2ea
            v = {k: s.get().strip() for k, s in self.vars.items()}
            if not (v["aasx"] and v["qea"]):
                self._say("⚠ Renseigner l'AASX source et le .qea cible (une copie !).")
                return
            self._say("Génération du modèle EA (peut prendre 1-2 min)…")
            stats = aas2ea.generate(v["aasx"], v["qea"], v["png"])
            self._say("✔ EA : %s" % stats)
            self._say("   → " + v["qea"])
        finally:
            pythoncom.CoUninitialize()

    def run_chain(self):
        self.run_udt2aas()
        self.run_aas2ea()

    # -- settings persistence -------------------------------------------------

    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                for k, val in json.load(f).items():
                    if k in self.vars and val:
                        self.vars[k].set(val)
        except Exception:
            pass

    def _save_settings(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump({k: s.get() for k, s in self.vars.items()}, f)
        except Exception:
            pass


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
