#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from datetime import datetime
import re
import sys

def backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    bak.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    print(f"✅ backup: {bak}")
    return bak

def patch_client_order_detail_ux(path: Path) -> None:
    s = path.read_text(encoding="utf-8", errors="replace")

    # 1) Isoler le bloc de fonction (du def jusqu'au prochain def décoré ou fin)
    m = re.search(
        r"(?ms)^@ensure_csrf_cookie\s*\n@client_login_required\s*\n"
        r"def\s+client_order_detail\(request,\s*order_id:\s*int\):\s*\n"
        r"(?P<body>.*?)(?=^\S|\Z)",
        s,
    )
    if not m:
        print("❌ client_order_detail() introuvable (pattern mismatch).")
        sys.exit(1)

    body = m.group("body")

    # Helper: remplace un bloc if ... return redirect(...) en gardant l'indent
    def _patch_redirect(body_txt: str, which: str, msg: str) -> tuple[str, bool]:
        # Match:
        # <indent>if not customer:
        # <indent>    return redirect("orders:client_home")
        # ou guillemets simples
        pat = re.compile(
            rf"(?m)^(?P<ind>\s*)if\s+not\s+{which}\s*:\s*\n"
            rf"(?P=ind)\s*return\s+redirect\(\s*(['\"])orders:client_home\2\s*\)\s*\n"
        )

        mm = pat.search(body_txt)
        if not mm:
            return body_txt, False

        ind = mm.group("ind")
        # Si déjà patché (messages.error déjà là), on ne touche pas
        already = re.search(
            rf"(?m)^{re.escape(ind)}\s*from\s+django\.contrib\s+import\s+messages\s*$",
            body_txt
        )
        if already:
            return body_txt, False

        repl = (
            f"{ind}if not {which}:\n"
            f"{ind}    from django.contrib import messages\n"
            f"{ind}    messages.error(request, {msg!r})\n"
            f"{ind}    return redirect(\"orders:client_home\")\n"
        )
        body_txt2 = body_txt[:mm.start()] + repl + body_txt[mm.end():]
        return body_txt2, True

    body2, ok1 = _patch_redirect(body, "customer", "Session client invalide. Reconnecte-toi.")
    body3, ok2 = _patch_redirect(body2, "order", "Commande introuvable ou accès non autorisé.")

    # 2) Ajouter no-store sur la réponse (transformer return render -> resp = render ; resp[...] ; return resp)
    # On ne le fait que si pas déjà présent dans la fonction.
    if "Cache-Control" not in body3:
        pat_render = re.compile(
            r"(?ms)^(?P<ind>\s*)return\s+render\(\s*request\s*,\s*"
            r"(['\"])orders/client_order_detail\.html\2\s*,\s*\{.*?\}\s*\)\s*$"
        )
        mm = pat_render.search(body3)
        if mm:
            ind = mm.group("ind")
            render_stmt = mm.group(0)
            # remplace seulement ce return render
            new = (
                re.sub(r"(?m)^\s*return\s+render\(", f"{ind}resp = render(", render_stmt, count=1)
                + f"\n{ind}resp[\"Cache-Control\"] = \"no-store\"\n{ind}return resp"
            )
            body3 = body3[:mm.start()] + new + body3[mm.end():]
            ok3 = True
        else:
            ok3 = False
    else:
        ok3 = False

    if not (ok1 or ok2 or ok3):
        print("ℹ️ Rien à patcher (déjà patché ou structure différente).")
        return

    # Réinjecter dans le fichier complet
    s2 = s[:m.start("body")] + body3 + s[m.end("body"):]
    path.write_text(s2, encoding="utf-8")
    print("✅ Patch client_order_detail_ux appliqué.")

def main():
    if len(sys.argv) < 2:
        print("Usage: tools/patch.py <patch-name>")
        sys.exit(2)

    name = sys.argv[1].strip()
    if name == "client_order_detail_ux":
        path = Path("orders/views.py")
        backup(path)
        patch_client_order_detail_ux(path)
        return

    print(f"❌ Patch inconnu: {name}")
    sys.exit(2)

if __name__ == "__main__":
    main()
