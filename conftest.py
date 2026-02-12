import pytest
from django.conf import settings

@pytest.fixture(autouse=True, scope="session")
def _enable_testing_flag():
    # Permet aux migrations/logiciels internes d’utiliser settings.TESTING
    setattr(settings, "TESTING", True)
