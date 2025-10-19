from django.db import models
from django.utils import timezone
import random
import string

class Commande(models.Model):
    TYPE_SERVICE_CHOICES = [
        ("BLANCHISSERIE", "Blanchisserie"),
        ("PRESSING", "Pressing"),
        ("COUTURE", "Retouche / Couture"),
        ("CORDONNERIE", "Cordonnerie"),
    ]

    STATUT_CHOICES = [
        ("NOUVELLE", "Nouvelle"),
        ("A_RAMASSER", "À ramasser"),
        ("EN_TRAITEMENT", "En traitement"),
        ("EN_LIVRAISON", "En livraison"),
        ("TERMINEE", "Terminée"),
    ]

    code = models.CharField(max_length=20, unique=True, editable=False)
    nom_client = models.CharField(max_length=120)
    telephone = models.CharField(max_length=40)
    adresse_livraison = models.TextField()
    type_service = models.CharField(max_length=20, choices=TYPE_SERVICE_CHOICES, default="BLANCHISSERIE")
    zone = models.CharField(max_length=120, blank=True)
    partenaire = models.CharField(max_length=120, blank=True, help_text="Laisser vide pour auto-assignation")
    instructions = models.TextField(blank=True)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="NOUVELLE")

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def _gen_code(self):
        rand = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        return f"FAGNI_{rand}"

    def save(self, *args, **kwargs):
        if not self.code:
            new = self._gen_code()
            # garantir unicité
            while Commande.objects.filter(code=new).exists():
                new = self._gen_code()
            self.code = new
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.nom_client}"