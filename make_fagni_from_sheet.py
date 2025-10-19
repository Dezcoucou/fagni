import sys, os, zipfile
from PIL import Image

def remove_bg_to_alpha(im, bg="black", thresh=25):
    """Rend transparent le fond quasi noir (thresh 20–35)."""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r,g,b,a = px[x,y]
            if bg == "black":
                if r < thresh and g < thresh and b < thresh:
                    px[x,y] = (0,0,0,0)
            elif bg == "white":
                if r > 255-thresh and g > 255-thresh and b > 255-thresh:
                    px[x,y] = (255,255,255,0)
    return im

def autocrop_rgba(im):
    """Rogne les marges vides en se basant sur l'alpha."""
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    alpha = im.split()[-1]
    bbox = alpha.getbbox()
    return im.crop(bbox) if bbox else im

def crop_quadrant(sheet, idx, cols=2, rows=2, margin=12):
    """Découpe un quadrant 1..4 d'une grille 2x2."""
    W, H = sheet.size
    w, h = W//cols, H//rows
    idx -= 1
    col, row = idx % cols, idx // cols
    x1 = col*w + margin
    y1 = row*h + margin
    x2 = (col+1)*w - margin
    y2 = (row+1)*h - margin
    return sheet.crop((x1, y1, x2, y2))

def main():
    if len(sys.argv) < 3:
        print("Usage: python make_fagni_from_sheet.py <sheet_image> <index 1-4>")
        sys.exit(1)

    sheet_path = sys.argv[1]
    index = int(sys.argv[2])  # 1=haut-gauche, 2=haut-droit, 3=bas-gauche, 4=bas-droit

    sheet = Image.open(sheet_path).convert("RGBA")
    logo = crop_quadrant(sheet, index, margin=12)

    # Transparent (enlève fond noir) + rognage
    logo_trans = remove_bg_to_alpha(logo, bg="black", thresh=25)
    logo_trans = autocrop_rgba(logo_trans)

    # Fond blanc
    white_bg = Image.new("RGB", logo_trans.size, (255,255,255))
    white_bg.paste(logo_trans, mask=logo_trans.split()[-1])

    base = f"fagni_logo_q{index}"
    out_trans = f"{base}_transparent.png"
    out_white = f"{base}_white.png"
    logo_trans.save(out_trans, "PNG")
    white_bg.save(out_white, "PNG")

    # ZIP
    zip_name = "fagni_logos.zip"
    with zipfile.ZipFile(zip_name, "w") as z:
        z.write(out_trans)
        z.write(out_white)

    print("✅ Fichiers créés :")
    print("-", out_trans)
    print("-", out_white)
    print("-", zip_name)

if __name__ == "__main__":
    main()