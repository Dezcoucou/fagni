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
        # Stockage local (Cloudinary non configuré en pilote)
        import os, base64, uuid
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        if isinstance(photo_data, str) and photo_data.startswith("data:"):
            header, encoded = photo_data.split(",", 1)
            ext = "png" if "png" in header else "webp" if "webp" in header else "jpg"
            raw = base64.b64decode(encoded)
        else:
            ext = "jpg"
            raw = base64.b64decode(str(photo_data))
        filename = f"orders/evidence/order_{order_id}_{photo_type}_driver{driver.id}_{uuid.uuid4().hex[:8]}.{ext}"
        saved_path = default_storage.save(filename, ContentFile(raw))
        url = default_storage.url(saved_path)

        # Stocker l'URL dans les notes
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

        # MVP terrain : stockage local si Cloudinary n'est pas configuré
        import os, base64, uuid
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        if isinstance(photo_data, str) and photo_data.startswith("data:"):
            header, encoded = photo_data.split(",", 1)
            ext = "jpg"
            if "png" in header:
                ext = "png"
            elif "webp" in header:
                ext = "webp"
            raw = base64.b64decode(encoded)
        else:
            ext = "jpg"
            raw = base64.b64decode(str(photo_data))

        filename = f"orders/evidence/order_{order_id}_{photo_type}_partner{partner.id}_{uuid.uuid4().hex[:8]}.{ext}"
        saved_path = default_storage.save(filename, ContentFile(raw))
        url = default_storage.url(saved_path)

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
        logger.exception("partner_upload_photo failed order_id=%s", order_id)
        return Response({'error': 'upload_photo_failed', 'details': str(e)}, status=400)
