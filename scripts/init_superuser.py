from django.contrib.auth import get_user_model

def run():
    User = get_user_model()
    username = "adminfagni"
    email = "admin@fagni.local"
    pwd = "ChangeMe!123"

    u, created = User.objects.get_or_create(username=username, defaults={"email": email})
    u.is_staff = True
    u.is_superuser = True
    u.set_password(pwd)
    u.save()

    if created:
        print(f"✅ Superuser '{username}' créé automatiquement.")
    else:
        print(f"♻️ Superuser '{username}' déjà existant, mot de passe mis à jour.")
