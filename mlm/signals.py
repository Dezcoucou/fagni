"""
MLM: déclenchement CANONIQUE = au paiement (payment_status == "paid"),
via Order.mark_as_paid_and_distribute().

On neutralise le post_save(Order) sur status="done" pour éviter les doublons
et les distributions avant paiement.
"""
# Intentionnellement vide.
