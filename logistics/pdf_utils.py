from io import BytesIO
from pathlib import Path

from django.http import HttpResponse
from django.utils import timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


PAGE_WIDTH, PAGE_HEIGHT = A4


def _draw_title(c, text, y):
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, y, text)
    return y - 24


def _draw_label_value(c, label, value, y):
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, f"{label} :")
    c.setFont("Helvetica", 10)
    c.drawString(150, y, str(value or "-"))
    return y - 16


def _draw_section_title(c, text, y):
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, text)
    c.line(40, y - 4, 300, y - 4)
    return y - 18


def _fit_image(path, max_width, max_height):
    img = ImageReader(path)
    iw, ih = img.getSize()
    ratio = min(max_width / iw, max_height / ih)
    return img, iw * ratio, ih * ratio


def build_mission_client_pdf(*, mission, latest_otp=None, latest_proof=None, latest_signature=None):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    y = PAGE_HEIGHT - 40
    y = _draw_title(c, "FAGNI - Preuve client de mission", y)

    y = _draw_label_value(c, "Mission", mission.code, y)
    y = _draw_label_value(c, "Commande", getattr(mission.order, "id", "-"), y)
    y = _draw_label_value(c, "Type", getattr(mission, "get_mission_type_display", lambda: mission.mission_type)(), y)
    y = _draw_label_value(c, "Statut", getattr(mission, "get_status_display", lambda: mission.status)(), y)
    y = _draw_label_value(c, "Client", mission.contact_name or "Client FAGNI", y)
    y = _draw_label_value(c, "Téléphone", mission.contact_phone or "-", y)
    y = _draw_label_value(c, "Date génération", timezone.localtime().strftime("%d/%m/%Y %H:%M"), y)

    y -= 10
    y = _draw_section_title(c, "Validation", y)

    otp_status = latest_otp.status if latest_otp else "absent"
    proof_status = latest_proof.status if latest_proof else "absente"
    signature_status = latest_signature.status if latest_signature else "absente"

    y = _draw_label_value(c, "OTP WhatsApp", otp_status, y)
    y = _draw_label_value(c, "Preuve photo", proof_status, y)
    y = _draw_label_value(c, "Signature", signature_status, y)

    if latest_otp and getattr(latest_otp, "verified_at", None):
        y = _draw_label_value(
            c,
            "OTP validé le",
            timezone.localtime(latest_otp.verified_at).strftime("%d/%m/%Y %H:%M"),
            y,
        )

    if latest_signature:
        y = _draw_label_value(c, "Signataire", latest_signature.signer_name or "-", y)

    if latest_proof and latest_proof.notes:
        y -= 8
        y = _draw_section_title(c, "Note terrain", y)
        c.setFont("Helvetica", 10)
        text_obj = c.beginText(40, y)
        for line in str(latest_proof.notes).splitlines():
            text_obj.textLine(line[:110])
        c.drawText(text_obj)
        y = text_obj.getY() - 12

    # Images
    if y < 260:
        c.showPage()
        y = PAGE_HEIGHT - 40

    if latest_proof and getattr(latest_proof, "photo", None):
        photo_path = Path(latest_proof.photo.path)
        if photo_path.exists():
            y = _draw_section_title(c, "Photo de preuve", y)
            try:
                img, w, h = _fit_image(str(photo_path), 240, 180)
                c.drawImage(img, 40, y - h, width=w, height=h, preserveAspectRatio=True, mask='auto')
                y = y - h - 20
            except Exception:
                y = _draw_label_value(c, "Photo", "Impossible à afficher", y)

    if latest_signature and getattr(latest_signature, "image", None):
        sig_path = Path(latest_signature.image.path)
        if sig_path.exists():
            if y < 220:
                c.showPage()
                y = PAGE_HEIGHT - 40
            y = _draw_section_title(c, "Signature client", y)
            try:
                img, w, h = _fit_image(str(sig_path), 240, 140)
                c.drawImage(img, 40, y - h, width=w, height=h, preserveAspectRatio=True, mask='auto')
                y = y - h - 20
            except Exception:
                y = _draw_label_value(c, "Signature", "Impossible à afficher", y)

    c.setFont("Helvetica-Oblique", 8)
    footer = "Document généré automatiquement par FAGNI"
    footer_width = stringWidth(footer, "Helvetica-Oblique", 8)
    c.drawString(PAGE_WIDTH - footer_width - 40, 20, footer)

    c.save()
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="mission_{mission.id}_preuve_client.pdf"'
    return response
