from django.db import migrations, models
from decimal import Decimal

class Migration(migrations.Migration):
    dependencies = [
        ("wallets", "0021_add_paiement_model"),
    ]
    operations = [
        migrations.AddField(
            model_name="wallet",
            name="balance_securite",
            field=models.DecimalField(
                verbose_name="Bonus sécurité (libéré lundi)",
                max_digits=12, decimal_places=2,
                default=Decimal("0.00"),
            ),
        ),
    ]
