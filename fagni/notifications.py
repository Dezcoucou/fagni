import logging
import os

import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger("fagni.notifications")

_app = None


def get_firebase_app():
    global _app

    if _app is None:
        cred_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "firebase_credentials.json",
        )
        cred = credentials.Certificate(cred_path)
        _app = firebase_admin.initialize_app(cred)

    return _app


def _safe_data(data=None):
    return {str(k): str(v) for k, v in (data or {}).items()}


def _short_token(token):
    token = token or ""
    if len(token) <= 18:
        return token
    return f"{token[:10]}...{token[-8:]}"


def send_push(token, title, body, data=None):
    """
    Envoie une notification FCM unique.

    Retourne True si Firebase accepte le message, False sinon.
    Les erreurs sont loggées avec assez d'information pour le diagnostic terrain.
    """
    if not token:
        logger.warning(
            "[FCM] skipped: empty token | title=%s | data=%s",
            title,
            _safe_data(data),
        )
        return False

    payload_data = _safe_data(data)

    try:
        get_firebase_app()

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=payload_data,
            token=token,
        )

        message_id = messaging.send(message)

        logger.info(
            "[FCM] success | message_id=%s | token=%s | title=%s | data=%s",
            message_id,
            _short_token(token),
            title,
            payload_data,
        )
        return True

    except messaging.UnregisteredError as e:
        logger.warning(
            "[FCM] unregistered token deleted | token=%s | title=%s | data=%s | error=%s",
            _short_token(token),
            title,
            payload_data,
            str(e),
        )
        try:
            from orders.models import FCMToken
            deleted, _ = FCMToken.objects.filter(token=token).delete()
            logger.warning(
                "[FCM] token cleanup | deleted=%s | token=%s",
                deleted,
                _short_token(token),
            )
        except Exception as cleanup_error:
            logger.exception(
                "[FCM] token cleanup failed | token=%s | error=%s",
                _short_token(token),
                str(cleanup_error),
            )
        return False

    except Exception as e:
        logger.exception(
            "[FCM] failed | token=%s | title=%s | data=%s | error=%s",
            _short_token(token),
            title,
            payload_data,
            str(e),
        )
        return False


def send_push_multi(tokens, title, body, data=None):
    success_count = 0
    total = 0

    for token in tokens:
        total += 1
        if send_push(token, title, body, data):
            success_count += 1

    logger.info(
        "[FCM] multi result | success=%s | total=%s | title=%s | data=%s",
        success_count,
        total,
        title,
        _safe_data(data),
    )
    return success_count


def notif_mission_assignee(token, order_code):
    return send_push(
        token,
        "Nouvelle mission FAGNI",
        "Commande " + str(order_code) + " - Acceptez vite",
        {"type": "mission", "order_code": order_code},
    )


def notif_pressing_commande(token, order_code):
    return send_push(
        token,
        "Nouvelle commande",
        "Commande " + str(order_code) + " en route vers vous",
        {"type": "order", "order_code": order_code},
    )


def notif_client_livraison(token, order_code):
    return send_push(
        token,
        "Votre linge arrive",
        "Commande " + str(order_code) + " - Votre livreur est en route",
        {"type": "delivery", "order_code": order_code},
    )


def notif_client_pret(token, order_code):
    return send_push(
        token,
        "Linge pret",
        "Commande " + str(order_code) + " - Votre pressing a termine",
        {"type": "ready", "order_code": order_code},
    )
