(function() {
  function addStepper(input, step, min, isDecimal) {
    if (!input) return;
    input.style.width = "7em";
    var wrap = document.createElement('div');
    wrap.style.display = "inline-flex";
    wrap.style.alignItems = "center";
    wrap.style.gap = ".25rem";

    var minus = document.createElement('button');
    minus.type = "button";
    minus.textContent = "−";
    minus.style.padding = ".2rem .5rem";

    var plus = document.createElement('button');
    plus.type = "button";
    plus.textContent = "+";
    plus.style.padding = ".2rem .5rem";

    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(minus);
    wrap.appendChild(input);
    wrap.appendChild(plus);

    function parseVal() {
      var v = isDecimal ? parseFloat(input.value || "0") : parseInt(input.value || "0", 10);
      return isNaN(v) ? 0 : v;
    }

    minus.addEventListener('click', function() {
      var v = parseVal() - step;
      if (v < min) v = min;
      input.value = isDecimal ? v.toFixed(2) : v;
      input.dispatchEvent(new Event('change', {bubbles:true}));
    });

    plus.addEventListener('click', function() {
      var v = parseVal() + step;
      input.value = isDecimal ? v.toFixed(2) : v;
      input.dispatchEvent(new Event('change', {bubbles:true}));
    });
  }

  function init() {
    // Quantité (entier, min 0)
    document.querySelectorAll("input[name$='-quantity']").forEach(function(inp){
      if (!inp.dataset.stepper) {
        addStepper(inp, 1, 0, false);
        inp.dataset.stepper = "1";
      }
    });
    // Poids estimé (décimal, pas 0.1, min 0)
    document.querySelectorAll("input[name$='-weight_kg']").forEach(function(inp){
      if (!inp.dataset.stepper) {
        addStepper(inp, 0.1, 0, true);
        inp.dataset.stepper = "1";
      }
    });
  }

  // Initialisation au chargement et lors de l'ajout d'un inline
  document.addEventListener('DOMContentLoaded', init);
  document.body && document.body.addEventListener('click', function(e){
    // Django admin ajoute des inlines dynamiquement avec le bouton "Ajouter un autre"
    if (e.target && e.target.classList && e.target.classList.contains('add-row')) {
      setTimeout(init, 50);
    }
  });
})();
