import pytest
from django.conf import settings

@pytest.fixture(autouse=True, scope="session")
def _enable_testing_flag():
    # Permet aux migrations/logiciels internes d'utiliser settings.TESTING
    setattr(settings, "TESTING", True)

@pytest.fixture(autouse=True, scope="session")
def _disable_manifest_staticfiles():
    # Override du backend static pour les tests
    # Utilise le backend standard au lieu du ManifestStaticFilesStorage
    settings.STORAGES = {
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        }
    }
