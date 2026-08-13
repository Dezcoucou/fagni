from types import SimpleNamespace

from django.test import SimpleTestCase

from orders.domain_canonical import infer_service_type_from_order_item


def _item(designation="", service=None):
    return SimpleNamespace(
        designation=designation,
        service=service,
    )


def _service(name="", category_name=""):
    category = None
    if category_name:
        category = SimpleNamespace(
            name=category_name,
            label="",
        )

    return SimpleNamespace(
        name=name,
        label="",
        designation="",
        category=category,
    )


class ServiceTypeInferenceTests(SimpleTestCase):
    def assertServiceType(self, designation, expected):
        actual = infer_service_type_from_order_item(
            _item(designation=designation)
        )
        self.assertEqual(actual, expected)

    def test_pantalon_is_pressing_not_cordonnerie(self):
        self.assertServiceType("Pantalon", "pressing")

    def test_plural_pantalons_is_pressing(self):
        self.assertServiceType("Pantalons", "pressing")

    def test_talon_is_cordonnerie(self):
        self.assertServiceType("Talon", "cordonnerie")

    def test_reparation_talon_is_cordonnerie(self):
        self.assertServiceType("Réparation talon", "cordonnerie")

    def test_semelle_chaussure_is_cordonnerie_by_priority(self):
        self.assertServiceType(
            "Semelle chaussure",
            "cordonnerie",
        )

    def test_ressemelage_chaussures_is_cordonnerie(self):
        self.assertServiceType(
            "Ressemelage chaussures",
            "cordonnerie",
        )

    def test_chaussure_is_chaussures(self):
        self.assertServiceType("Chaussure", "chaussures")

    def test_basket_is_chaussures(self):
        self.assertServiceType("Basket", "chaussures")

    def test_sneakers_is_chaussures(self):
        self.assertServiceType("Sneakers", "chaussures")

    def test_ourlet_pantalon_is_retouche_by_priority(self):
        self.assertServiceType(
            "Ourlet pantalon",
            "retouche",
        )

    def test_retouche_pantalon_is_retouche(self):
        self.assertServiceType(
            "Retouche pantalon",
            "retouche",
        )

    def test_reprise_robe_is_retouche(self):
        self.assertServiceType(
            "Reprise robe",
            "retouche",
        )

    def test_common_pressing_items(self):
        for designation in (
            "Jean",
            "Chemise",
            "Robe",
            "Jupe",
            "T-shirt",
            "Polo",
        ):
            with self.subTest(designation=designation):
                self.assertServiceType(
                    designation,
                    "pressing",
                )

    def test_hyphenated_tshirt_is_normalized(self):
        self.assertServiceType(
            "T-shirt",
            "pressing",
        )

    def test_service_category_can_drive_classification(self):
        item = _item(
            designation="Prestation premium",
            service=_service(
                name="Service premium",
                category_name="Retouche",
            ),
        )

        self.assertEqual(
            infer_service_type_from_order_item(item),
            "retouche",
        )

    def test_unknown_item_preserves_legacy_pressing_default(self):
        self.assertServiceType(
            "Article inconnu",
            "pressing",
        )
