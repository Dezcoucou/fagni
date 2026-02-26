#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from datetime import datetime
import re
import sys

FILES = [
    Path("orders/templates/orders/_client_topbar.html"),
    Path("orders/templates/orders/client_order_detail.html"),
    Path("orders/templates/orders/client_home.html"),
]

def backup(p: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak.{ts}")
    bak.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    print(f"✅ backup: {bak}")

def patch_file(p: Path) -> int:
    s = p.read_text(encoding="utf-8", errors="replace")

    # remplace <a ... href="{% url 'orders:client_logout' %}" ...>Déconnexion</a>
    # en <form method="post"...> ... <button ...>Déconnexion</button></form>
    def repl(m: re.Match) -> str:
        cls = m.group("cls") or ""
        cls = cls.strip()

        # si pas de classe, fallback
        btn_class = cls if cls else "btn btn-light"

        # conserve style existant du <a> s'il y en a un
        astyle = (m.group("style") or "").strip()
        btn_style = "background:transparent;border:0;cursor:pointer;text-decoration:none;"
        if astyle:
            # on garde le style du <a> ET on ajoute les safety
            btn_style = astyle.rstrip(";") + ";" + btn_style

        return (
            '<form method="post" action="{% url \'orders:client_logout\' %}" style="display:inline;">\n'
            '  {% csrf_token %}\n'
            f'  <button type="submit" class="{btn_class}" style="{btn_style}">Déconnexion</button>\n'
            '</form>'
        )

    # regex robuste: classe et style optionnels
    pat = re.compile(
        r'<a\b(?P<attrs>[^>]*?)\bhref="\{\%\s*url\s+\'orders:client_logout\'\s*\%\}"(?P<attrs2>[^>]*?)>\s*Déconnexion\s*</a>',
        re.IGNORECASE
    )

    # extraire class/style si présents
    def _extract(attr_blob: str):
        cls = None
        style = None
        m1 = re.search(r'\bclass="([^"]+)"', attr_blob)
        if m1: cls = m1.group(1)
        m2 = re.search(r'\bstyle="([^"]+)"', attr_blob)
        if m2: style = m2.group(1)
        return cls, style

    changed = 0
    out = []
    last = 0
    for m in pat.finditer(s):
        attrs = (m.group("attrs") or "") + " " + (m.group("attrs2") or "")
        cls, style = _extract(attrs)
        # construit une fausse match avec groupes nommés cls/style
        class Fake:
            def group(self, k):
                return {"cls": cls, "style": style}[k]
        out.append(s[last:m.start()])
        out.append(repl(Fake()))
        last = m.end()
        changed += 1
    out.append(s[last:])
    s2 = "".join(out)

    if changed:
        p.write_text(s2, encoding="utf-8")
    return changed

def main():
    total = 0
    for p in FILES:
        if not p.exists():
            print(f"⚠️ introuvable: {p}")
            continue
        backup(p)
        n = patch_file(p)
        print(f"✅ {p}: replaced={n}")
        total += n

    if total == 0:
        print("❌ Aucun lien logout trouvé. Rien modifié.")
        sys.exit(1)

    print("✅ Logout links converted to POST forms.")

if __name__ == "__main__":
    main()
