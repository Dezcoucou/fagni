(function(){
  function parseDec(x){ const v=parseFloat((x||'').toString().replace(',', '.')); return isNaN(v)?0:v; }

  function compute(){
    // Sommes live
    let totalQty = 0;
    let totalKg  = 0;
    let totalEUR = 0;

    // Champs de chaque ligne Inline
    document.querySelectorAll('.dynamic-orderitem_set, .inline-related').forEach(function(row){
      const q   = row.querySelector("input[name$='-quantity']");
      const kg  = row.querySelector("input[name$='-weight_kg']");
      const pr  = row.querySelector("input[name$='-price']");     // si affiché en lecture/écriture
      const unit= row.querySelector("select[name$='-unit']");     // optionnel

      if(q)  totalQty += parseDec(q.value);
      if(kg) totalKg  += parseDec(kg.value);
      // Si un champ 'price' existe, on l’additionne pour donner un total € live
      if(pr) totalEUR += parseDec(pr.value);

      // BONUS : si pas de champ 'price', on tente une estimation via data-price sur l’option sélectionnée
      if(!pr && unit){
        const itemSel = row.querySelector("select[name$='-item']");
        if(itemSel && itemSel.selectedOptions.length){
          const opt = itemSel.selectedOptions[0];
          const u   = (unit.value||'').toUpperCase();
          const pKg   = parseDec(opt.dataset.priceKg);
          const pUnit = parseDec(opt.dataset.priceUnit);
          if(u === 'KG' && kg){
            totalEUR += pKg * parseDec(kg.value);
          }else if(q){
            totalEUR += pUnit * parseDec(q.value);
          }
        }
      }
    });

    // Affichage dans le bandeau admin
    let panel = document.getElementById('live-total-panel');
    if(!panel){
      panel = document.createElement('div');
      panel.id = 'live-total-panel';
      panel.style.cssText = "position:sticky;top:0;z-index:9999;background:#0a7; color:#fff; padding:6px 10px; font-weight:600; border-bottom:2px solid #086;";
      const ct = document.querySelector('#content') || document.body;
      ct.prepend(panel);
    }
    panel.textContent = `Total live — Qté: ${totalQty.toFixed(0)} · Kg: ${totalKg.toFixed(2)} · €: ${totalEUR.toFixed(2)}`;
  }

  // Recalcule au chargement + lors des saisies / ajouts d'inlines
  function bind(){
    document.addEventListener('input', function(e){
      const n = e.target && e.target.name || '';
      if (/-quantity$|-weight_kg$|-price$|-unit$|-item$/.test(n)) compute();
    });
    // Quand on clique “Ajouter un autre (inline)”
    document.body && document.body.addEventListener('click', function(e){
      if(e.target && e.target.classList && e.target.classList.contains('add-row')){
        setTimeout(compute, 80);
      }
    });
    compute();
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
