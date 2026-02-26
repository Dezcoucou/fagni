#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from datetime import datetime
import re
import sys

TARGETS = [
    Path("orders/templates/orders/client_base.html"),
    Path("templates/base.html"),
]

START = "<!-- __FAGNI_WHITE_DOCTOR__ -->"
END   = "<!-- __FAGNI_WHITE_DOCTOR__ END -->"

DOCTOR = r"""<!-- __FAGNI_WHITE_DOCTOR__ -->
<script>
(function(){
  try{
    var host = (location && location.hostname) ? location.hostname : "";
    var isLocal = (host === "127.0.0.1" || host === "localhost");
    if(!isLocal) return;

    function css(el){ try{ return getComputedStyle(el); }catch(e){ return null; } }
    function fmtEl(el){
      if(!el) return "null";
      var id = el.id ? ("#"+el.id) : "";
      var cls = (el.className && typeof el.className==="string") ? ("."+el.className.trim().replace(/\s+/g,".")) : "";
      return (el.tagName||"EL")+id+cls;
    }
    function pickTop(){
      try{
        var x=Math.floor(window.innerWidth/2), y=Math.floor(window.innerHeight/2);
        return document.elementFromPoint(x,y);
      }catch(e){ return null; }
    }
    function isFullFixed(st){
      if(!st || st.position!=="fixed") return false;
      var inset=(st.inset||"").replace(/\s+/g,"");
      var full=(inset==="0px"||inset==="0");
      var top0=(st.top==="0px"||st.top==="0");
      var left0=(st.left==="0px"||st.left==="0");
      var right0=(st.right==="0px"||st.right==="0");
      var bottom0=(st.bottom==="0px"||st.bottom==="0");
      return full || (top0 && left0 && right0 && bottom0);
    }
    function nukeOverlays(){
      var nuked=[];
      try{
        Array.from(document.querySelectorAll("body *")).forEach(function(el){
          try{
            var st=css(el); if(!st) return;
            if(st.display==="none") return;
            var z=parseInt(st.zIndex||"0",10)||0;
            if(z<999) return;
            if(!isFullFixed(st)) return;
            el.setAttribute("data-fagni-nuked","1");
            el.style.setProperty("display","none","important");
            nuked.push(fmtEl(el)+" z="+z);
          }catch(e){}
        });
      }catch(e){}
      return nuked;
    }
    function forceVisible(){
      try{
        document.documentElement.style.setProperty("opacity","1","important");
        document.documentElement.style.setProperty("visibility","visible","important");
        document.documentElement.style.setProperty("display","block","important");
      }catch(e){}
      try{
        document.body.style.setProperty("opacity","1","important");
        document.body.style.setProperty("visibility","visible","important");
        document.body.style.setProperty("display","block","important");
        document.body.style.setProperty("overflow","auto","important");
        document.body.style.setProperty("background","#fff","important");
        document.body.style.setProperty("color","#0f172a","important");
        document.body.style.setProperty("min-height","100vh","important");
      }catch(e){}
      try{
        var shell=document.querySelector(".fagni-shell");
        if(shell){
          shell.style.setProperty("background","#fff","important");
          shell.style.setProperty("color","#0f172a","important");
          shell.style.setProperty("min-height","100vh","important");
        }
      }catch(e){}
    }
    function makePanel(){
      var d=document.getElementById("fagniWhiteDoctor");
      if(d) return d;
      d=document.createElement("div");
      d.id="fagniWhiteDoctor";
      d.style.cssText=[
        "position:fixed","left:10px","top:10px","z-index:2147483647",
        "max-width:min(520px,92vw)",
        "font-family:ui-monospace,Menlo,Consolas,monospace",
        "font-size:11px","line-height:1.35","color:#0f172a",
        "background:rgba(255,255,255,0.92)","border:1px solid rgba(2,6,23,0.18)",
        "border-radius:12px","padding:10px",
        "box-shadow:0 18px 40px rgba(2,6,23,0.25)"
      ].join(";");
      d.innerHTML="<div style='font-weight:900;margin-bottom:6px'>FAGNI White Doctor (local)</div><pre id='fagniWDPre' style='margin:0;white-space:pre-wrap'></pre>";
      document.body.appendChild(d);
      return d;
    }
    function report(){
      forceVisible();
      makePanel();
      var pre=document.getElementById("fagniWDPre");
      if(!pre) return;
      var top=pickTop();
      var topSt=top?css(top):null;
      var bSt=css(document.body);
      var hSt=css(document.documentElement);
      var nuked=nukeOverlays();

      var lines=[];
      lines.push("url: "+location.pathname+location.search);
      lines.push("viewport: "+window.innerWidth+"x"+window.innerHeight);
      lines.push("");
      lines.push("HTML: display="+(hSt&&hSt.display)+" vis="+(hSt&&hSt.visibility)+" op="+(hSt&&hSt.opacity));
      lines.push("BODY: display="+(bSt&&bSt.display)+" vis="+(bSt&&bSt.visibility)+" op="+(bSt&&bSt.opacity)+" bg="+(bSt&&bSt.backgroundColor)+" color="+(bSt&&bSt.color));
      lines.push("");
      lines.push("TOP@center: "+fmtEl(top));
      if(topSt){
        lines.push("  top: pos="+topSt.position+" inset="+topSt.inset+" z="+topSt.zIndex+" disp="+topSt.display+" vis="+topSt.visibility+" op="+topSt.opacity+" bg="+topSt.backgroundColor);
      }
      lines.push("");
      lines.push("NUKED overlays: "+(nuked.length?nuked.length:"none"));
      if(nuked.length){ nuked.slice(0,12).forEach(function(x){ lines.push("  - "+x); }); }

      pre.textContent=lines.join("\n");
      try{ console.log("[FAGNI WhiteDoctor]", lines.join(" | ")); }catch(e){}
    }

    if(document.readyState==="loading"){
      document.addEventListener("DOMContentLoaded", function(){
        report(); setTimeout(report,300); setTimeout(report,1200);
      });
    }else{
      report(); setTimeout(report,300); setTimeout(report,1200);
    }
  }catch(e){ try{ console.log("[FAGNI WhiteDoctor] error", e); }catch(_e){} }
})();
</script>
<!-- __FAGNI_WHITE_DOCTOR__ END -->"""

def backup(p: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak.wd.{ts}")
    bak.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    print(f"✅ backup: {bak}")

def inject(p: Path) -> bool:
    if not p.exists():
        return False

    s = p.read_text(encoding="utf-8", errors="replace")

    # purge ancien bloc
    s = re.sub(r"(?s)\n?\s*<!-- __FAGNI_WHITE_DOCTOR__ -->.*?<!-- __FAGNI_WHITE_DOCTOR__ END -->\s*\n?", "\n", s)

    if "</body>" in s:
        s2 = s.replace("</body>", "\n"+DOCTOR+"\n</body>", 1)
    elif "</html>" in s:
        s2 = s.replace("</html>", "\n"+DOCTOR+"\n</html>", 1)
    else:
        print(f"❌ {p}: ni </body> ni </html> trouvé")
        return True

    backup(p)
    p.write_text(s2, encoding="utf-8")
    print(f"✅ injected: {p}")
    return True

hit = False
for t in TARGETS:
    r = inject(t)
    hit = hit or r

if not hit:
    print("❌ Aucun template cible trouvé.")
    sys.exit(1)
