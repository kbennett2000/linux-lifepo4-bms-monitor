from PIL import Image, ImageDraw, ImageFilter

BG = (11, 13, 18)
ASSETS = "/home/kb/Desktop/projects/linux-lifepo4-bms-monitor/assets"

hero = Image.open("/tmp/shots/hero.png").convert("RGB")
dash = Image.open(f"{ASSETS}/dashboard-dark.png").convert("RGB")

W = hero.width  # 2560
pad_x = 110
gap = 24
bottom = 90

# scale dashboard to fit inner width
inner_w = W - 2 * pad_x
dash_w = inner_w
dash_h = round(dash.height * dash_w / dash.width)
dash = dash.resize((dash_w, dash_h), Image.LANCZOS)

# round the corners of the dashboard + draw a subtle border
radius = 28
mask = Image.new("L", (dash_w, dash_h), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, dash_w - 1, dash_h - 1], radius, fill=255)
rounded = Image.new("RGB", (dash_w, dash_h), BG)
rounded.paste(dash, (0, 0), mask)
border = ImageDraw.Draw(rounded)
border.rounded_rectangle([0, 0, dash_w - 1, dash_h - 1], radius, outline=(45, 99, 90), width=3)

H = hero.height + gap + dash_h + bottom
canvas = Image.new("RGB", (W, H), BG)
canvas.paste(hero, (0, 0))

# soft drop shadow behind the framed dashboard
dash_y = hero.height + gap
shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
sd = ImageDraw.Draw(shadow)
sd.rounded_rectangle([pad_x, dash_y + 10, pad_x + dash_w, dash_y + dash_h + 22], radius + 6, fill=(0, 0, 0, 150))
shadow = shadow.filter(ImageFilter.GaussianBlur(22))
canvas.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shadow.split()[3])

canvas.paste(rounded, (pad_x, dash_y), mask)
canvas.save(f"{ASSETS}/banner.png")
print("banner.png", canvas.size)
