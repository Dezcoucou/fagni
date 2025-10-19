from PIL import Image

# Ouvre ton logo source (attention : vérifie que ce nom de fichier existe bien)
logo = Image.open("static/img/fagni_logo_q4_transparent.png")

# Crée un favicon PNG (256x256)
logo_png = logo.copy()
logo_png = logo_png.resize((256, 256), Image.LANCZOS)
logo_png.save("static/img/favicon.png", format="PNG")

# Crée un favicon ICO multi-tailles
sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
logo.save("static/img/favicon.ico", format="ICO", sizes=sizes)

print("✅ Favicons générés : favicon.png et favicon.ico dans static/img/")
