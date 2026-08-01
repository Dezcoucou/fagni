import re

# Point unique de normalisation des numeros de telephone ivoiriens. Utilise
# par PilotWhitelist (orders/models.py) et par les points de controle
# api_login/api_register (orders/client_api.py) - un numero ne doit jamais
# creer plusieurs entrees ou correspondances distinctes selon la forme sous
# laquelle il a ete saisi (07..., 225.0..., +225 0..., 00225 0..., avec ou
# sans espaces/tirets).


def normalize_phone(raw):
    """
    Normalise un numero de telephone ivoirien vers un format canonique
    local a 10 chiffres (ex: '0700000001').

    Accepte notamment :
      - '07 00 00 00 01', '07-00-00-00-01'  (local, avec separateurs)
      - '2250700000001'                      (indicatif pays sans '+')
      - '+2250700000001'                     (indicatif pays avec '+')
      - '00225 0700000001'                   (prefixe international '00')
      - '225700000001'                       (indicatif pays, zero initial omis)

    Retourne une chaine vide si `raw` est vide/None. Ne valide pas la
    longueur/le prefixe du resultat : la validite est du ressort de
    l'appelant (voir le rapport de audit_pilot_whitelist_candidates pour
    la detection de numeros suspects).
    """
    if not raw:
        return ''
    digits = re.sub(r'\D', '', str(raw))
    if digits.startswith('00225'):
        digits = digits[5:]
    elif digits.startswith('225'):
        digits = digits[3:]
    if len(digits) == 9 and not digits.startswith('0'):
        digits = '0' + digits
    return digits
