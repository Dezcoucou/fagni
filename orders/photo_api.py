"""API Upload Photos FAGNI — Cloudinary"""
import jwt
import cloudinary
import cloudinary.uploader
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
import base64


def _get_driver(request):
    token = request.headers.get('Authorization','').replace('Bearer ','')
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    from partners.models import DeliveryPartner
    return DeliveryPartner.objects.get(id=payload['did'])


def _get_partner(request):
    token = request.headers.get('Authorization','').replace('Bearer ','')
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    from partners.models import LaundryPartner
    return LaundryPartner.objects.get(id=payload['pid'])


MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 Mo max avant décodage
MAX_PHOTO_DIMENSION = 4000  # pixels, largeur ou hauteur max


def _safe_decode_image(photo_data):
    """
    Decode et valide une image envoyee en base64 (avec ou sans prefixe data:).
    Re-encode l'image via Pillow pour neutraliser tout payload cache
    (metadonnees, contenu non-image deguise, exploits de parsing).
    Retourne (raw_bytes, extension) ou leve ValueError si invalide.
    """
    import base64
    from io import BytesIO
    from PIL import Image

    if isinstance(photo_data, str) and photo_data.startswith("data:"):
        try:
            header, encoded = photo_data.split(",", 1)
        except ValueError:
            raise ValueError("Format data URI invalide")
    else:
        encoded = str(photo_data)

    # Limite de taille sur la chaine base64 avant tout decodage (evite DoS mémoire)
    if len(encoded) > MAX_PHOTO_BYTES * 2:
        raise ValueError("Photo trop volumineuse")

    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        raise ValueError("Contenu base64 invalide")

    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("Photo trop volumineuse")

    if len(raw) == 0:
        raise ValueError("Photo vide")

    # Verification stricte : Pillow doit reconnaitre un vrai fichier image
    try:
        img = Image.open(BytesIO(raw))
        img.verify()
        # verify() invalide l'objet, il faut le rouvrir pour l'utiliser
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception:
        raise ValueError("Le fichier fourni n'est pas une image valide")

    if img.width > MAX_PHOTO_DIMENSION or img.height > MAX_PHOTO_DIMENSION:
        raise ValueError("Dimensions de l'image trop grandes")

    # Re-encodage complet : neutralise EXIF, metadonnees, et tout octet
    # cache apres les donnees d'image (technique de polyglotte de fichier)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        ext = "png"
        out = BytesIO()
        img.save(out, format="PNG", optimize=True)
    else:
        img = img.convert("RGB")
        ext = "jpg"
        out = BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)

    return out.getvalue(), ext


def _upload_to_cloudinary(data_uri, folder, public_id):
    return cloudinary.uploader.upload(
        data_uri,
        folder=f"fagni/{folder}",
        public_id=public_id,
        overwrite=True
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def driver_upload_photo(request, order_id):
    """POST /api/driver/orders/<id>/photo/ — {photo: base64, type: pickup|delivery}"""
    try:
        driver = _get_driver(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order
        order = Order.objects.get(id=order_id)
        photo_data = request.data.get('photo', '')
        photo_type = request.data.get('type', 'pickup')

        if not photo_data:
            return Response({'error': 'Photo manquante'}, status=400)

        public_id = f"order_{order_id}_{photo_type}_driver{driver.id}"

        # Stockage local (Cloudinary non configure en pilote)
        import os, uuid
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        try:
            raw, ext = _safe_decode_image(photo_data)
        except ValueError as ve:
            return Response({'error': 'photo_invalide', 'details': str(ve)}, status=400)

        filename = f"orders/evidence/order_{order_id}_{photo_type}_driver{driver.id}_{uuid.uuid4().hex[:8]}.{ext}"
        saved_path = default_storage.save(filename, ContentFile(raw))
        url = default_storage.url(saved_path)

        # Enregistrement dans OrderEvidencePhoto (source unifiee pour OPS)
        try:
            from orders.models import OrderEvidencePhoto
            KIND_MAP_DRIVER = {'pickup': 'pickup_items', 'delivery': 'delivery_to_client'}
            kind = KIND_MAP_DRIVER.get(photo_type, 'pickup_items')
            evidence = OrderEvidencePhoto(
                order=order,
                actor_type='driver', actor_id=driver.id,
                kind=kind,
            )
            from django.core.files.base import ContentFile as _CF
            evidence.image.save(filename.split('/')[-1], _CF(raw), save=True)
        except Exception:
            import logging
            logging.getLogger("fagni.photo_api").exception(
                "Echec creation OrderEvidencePhoto (driver) order_id=%s kind=%s", order_id, kind
            )

        # Stocker l'URL dans les notes (compatibilite ascendante)
        notes = order.notes or ''
        tag = f'PHOTO_{photo_type.upper()}:{url}'
        if tag not in notes:
            order.notes = notes + f'\n{tag}'
            order.save(update_fields=['notes', 'updated_at'])

        return Response({'success': True, 'url': url, 'type': photo_type})
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("driver_upload_photo failed order_id=%s", order_id)
        return Response({'error': 'upload_photo_failed', 'details': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([AllowAny])
def partner_upload_photo(request, order_id):
    """POST /api/partner/orders/<id>/photo/ — {photo: base64, type: before|after}"""
    try:
        partner = _get_partner(request)
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    try:
        from orders.models import Order
        order = Order.objects.get(id=order_id, laundry_partner=partner)
        photo_data = (
            request.data.get('photo')
            or request.data.get('image')
            or request.data.get('file')
            or request.data.get('photo_file')
            or ''
        )
        photo_type = request.data.get('type') or request.data.get('photo_type') or 'before'

        # Compatibilité multipart/form-data : fichier uploadé au lieu de base64
        if not photo_data and request.FILES:
            uploaded = (
                request.FILES.get('photo')
                or request.FILES.get('image')
                or request.FILES.get('file')
                or request.FILES.get('photo_file')
            )
            if uploaded:
                import base64
                raw = uploaded.read()
                content_type = getattr(uploaded, 'content_type', None) or 'image/jpeg'
                photo_data = 'data:%s;base64,%s' % (
                    content_type,
                    base64.b64encode(raw).decode('ascii')
                )

        if not photo_data:
            return Response({
                'error': 'photo_manquante',
                'message': 'Aucune photo reçue. Champs acceptés: photo, image, file, photo_file.',
                'data_keys': list(request.data.keys()),
                'file_keys': list(request.FILES.keys()),
            }, status=400)

        # MVP terrain : stockage local si Cloudinary n'est pas configure
        import os, uuid
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        try:
            raw, ext = _safe_decode_image(photo_data)
        except ValueError as ve:
            return Response({'error': 'photo_invalide', 'details': str(ve)}, status=400)

        filename = f"orders/evidence/order_{order_id}_{photo_type}_partner{partner.id}_{uuid.uuid4().hex[:8]}.{ext}"
        saved_path = default_storage.save(filename, ContentFile(raw))
        url = default_storage.url(saved_path)

        # Enregistrement dans OrderEvidencePhoto (source unifiee pour OPS)
        try:
            from orders.models import OrderEvidencePhoto
            KIND_MAP_PARTNER = {'before': 'partner_before', 'after': 'partner_after'}
            kind = KIND_MAP_PARTNER.get(photo_type, 'partner_before')
            evidence = OrderEvidencePhoto(
                order=order,
                actor_type='laundry', actor_id=partner.id,
                kind=kind,
            )
            from django.core.files.base import ContentFile as _CF
            evidence.image.save(filename.split('/')[-1], _CF(raw), save=True)
        except Exception:
            import logging
            logging.getLogger("fagni.photo_api").exception(
                "Echec creation OrderEvidencePhoto (partner) order_id=%s kind=%s", order_id, kind
            )

        notes = order.notes or ''
        tag = f'PHOTO_{photo_type.upper()}:{url}'
        if tag not in notes:
            order.notes = notes + f'\n{tag}'
            order.save(update_fields=['notes', 'updated_at'])

        return Response({'success': True, 'url': url, 'type': photo_type})
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("partner_upload_photo failed order_id=%s", order_id)
        return Response({'error': 'upload_photo_failed', 'details': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([AllowAny])
def order_photos(request, order_id):
    """GET /api/ops/orders/<id>/photos/ — toutes les photos d'une commande"""
    try:
        token = request.headers.get('Authorization','').replace('Bearer ','')
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        if not payload.get('ops'):
            raise Exception()
    except:
        return Response({'error': 'Non autorisé'}, status=401)

    from orders.models import Order
    try:
        order = Order.objects.get(id=order_id)
        notes = order.notes or ''
        photos = {}
        for line in notes.split('\n'):
            if line.startswith('PHOTO_'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    key = parts[0].replace('PHOTO_', '').lower()
                    photos[key] = parts[1].strip()
        return Response({'order_id': order_id, 'photos': photos})
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("order_photos failed order_id=%s", order_id)
        return Response({'error': 'upload_photo_failed', 'details': str(e)}, status=400)
