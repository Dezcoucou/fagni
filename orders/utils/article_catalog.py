"""
FAGNI — Catalogue articles pour le Smart Bag Builder
Règle : seule source de vérité pour les poids et slots
Ne jamais dupliquer ces valeurs dans React.
"""

ARTICLE_CATALOG = [
    {
        "category": "Hauts",
        "emoji": "👕",
        "items": [
            {"id": "tshirt",   "label": "T-shirt",       "emoji": "👕", "slots": 1, "weight_kg": 0.20},
            {"id": "chemise",  "label": "Chemise",        "emoji": "👔", "slots": 1, "weight_kg": 0.30},
            {"id": "polo",     "label": "Polo",           "emoji": "👕", "slots": 1, "weight_kg": 0.25},
            {"id": "debardeur","label": "Débardeur",      "emoji": "🎽", "slots": 1, "weight_kg": 0.15},
        ]
    },
    {
        "category": "Bas",
        "emoji": "👖",
        "items": [
            {"id": "pantalon", "label": "Pantalon",       "emoji": "👖", "slots": 1, "weight_kg": 0.50},
            {"id": "jean",     "label": "Jean",           "emoji": "👖", "slots": 1, "weight_kg": 0.60},
            {"id": "short",    "label": "Short",          "emoji": "🩳", "slots": 1, "weight_kg": 0.30},
            {"id": "jupe",     "label": "Jupe",           "emoji": "👗", "slots": 1, "weight_kg": 0.40},
            {"id": "legging",  "label": "Legging",        "emoji": "🩱", "slots": 1, "weight_kg": 0.25},
        ]
    },
    {
        "category": "Robes & Ensembles",
        "emoji": "👗",
        "items": [
            {"id": "robe",         "label": "Robe",             "emoji": "👗", "slots": 1, "weight_kg": 0.50},
            {"id": "robe_longue",  "label": "Robe longue",      "emoji": "👘", "slots": 2, "weight_kg": 0.80},
            {"id": "ensemble",     "label": "Ensemble 2 pièces","emoji": "👔", "slots": 2, "weight_kg": 0.90},
            {"id": "costume",      "label": "Costume complet",  "emoji": "🤵", "slots": 3, "weight_kg": 1.50},
            {"id": "boubou",       "label": "Boubou",           "emoji": "👘", "slots": 2, "weight_kg": 0.80},
        ]
    },
    {
        "category": "Manteaux & Vestes",
        "emoji": "🧥",
        "items": [
            {"id": "veste",    "label": "Veste / Blazer",  "emoji": "🥼", "slots": 2, "weight_kg": 0.80},
            {"id": "manteau",  "label": "Manteau",         "emoji": "🧥", "slots": 2, "weight_kg": 1.20},
            {"id": "parka",    "label": "Parka / Doudoune","emoji": "🧥", "slots": 3, "weight_kg": 1.50},
        ]
    },
    {
        "category": "Sous-vêtements & Chaussettes",
        "emoji": "🧦",
        "items": [
            {"id": "sous_vetement", "label": "Sous-vêtement", "emoji": "🩲", "slots": 1, "weight_kg": 0.10},
            {"id": "chaussettes",   "label": "Chaussettes (paire)", "emoji": "🧦", "slots": 1, "weight_kg": 0.10},
            {"id": "pyjama",        "label": "Pyjama",        "emoji": "🛌", "slots": 2, "weight_kg": 0.50},
        ]
    },
]

# Articles hors gabarit — commande séparée
HORS_GABARIT = [
    {"id": "couette",   "label": "Couette / Duvet",   "emoji": "🛏️", "note": "Commande séparée"},
    {"id": "drap",      "label": "Drap de lit",        "emoji": "🏠",  "note": "Commande séparée"},
    {"id": "rideau",    "label": "Rideau",             "emoji": "🪟",  "note": "Commande séparée"},
    {"id": "couverture","label": "Couverture épaisse", "emoji": "🧣",  "note": "Commande séparée"},
]
