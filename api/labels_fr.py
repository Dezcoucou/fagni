def _set_verbose(model, singular, plural):
    model._meta.verbose_name = singular
    model._meta.verbose_name_plural = plural

try:
    from .models import Article, ItemPhoto
except Exception:
    Article = ItemPhoto = None

if Article:
    _set_verbose(Article, "Article", "Articles")

if ItemPhoto:
    _set_verbose(ItemPhoto, "Photo d’article", "Photos d’article")
