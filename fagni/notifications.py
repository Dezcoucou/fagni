import firebase_admin
from firebase_admin import credentials, messaging
import os

_app = None

def get_firebase_app():
    global _app
    if _app is None:
        cred_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'firebase_credentials.json')
        cred = credentials.Certificate(cred_path)
        _app = firebase_admin.initialize_app(cred)
    return _app

def send_push(token, title, body, data=None):
    try:
        get_firebase_app()
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=token,
        )
        messaging.send(message)
        return True
    except Exception as e:
        print(f"[FCM] Erreur: {e}")
        return False

def send_push_multi(tokens, title, body, data=None):
    return sum(1 for t in tokens if send_push(t, title, body, data))

def notif_mission_assignee(token, order_code):
    return send_push(token, "Nouvelle mission FAGNI", "Commande " + order_code + " - Acceptez vite", {"type": "mission", "order_code": order_code})

def notif_pressing_commande(token, order_code):
    return send_push(token, "Nouvelle commande", "Commande " + order_code + " en route vers vous", {"type": "order", "order_code": order_code})

def notif_client_livraison(token, order_code):
    return send_push(token, "Votre linge arrive", "Commande " + order_code + " - Votre livreur est en route", {"type": "delivery", "order_code": order_code})

def notif_client_pret(token, order_code):
    return send_push(token, "Linge pret", "Commande " + order_code + " - Votre pressing a termine", {"type": "ready", "order_code": order_code})
