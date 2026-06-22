import sys
from PIL import Image

def autocrop(src, dst, bg, pad=48, thresh=18):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    px = im.load()
    br, bgc, bb = bg
    # find content bounding box: rows/cols differing from bg
    def row_has_content(y):
        for x in range(0, w, 7):
            r, g, b = px[x, y]
            if abs(r-br)+abs(g-bgc)+abs(b-bb) > thresh:
                return True
        return False
    def col_has_content(x):
        for y in range(0, h, 7):
            r, g, b = px[x, y]
            if abs(r-br)+abs(g-bgc)+abs(b-bb) > thresh:
                return True
        return False
    top = next((y for y in range(h) if row_has_content(y)), 0)
    bot = next((y for y in range(h-1, -1, -1) if row_has_content(y)), h-1)
    left = next((x for x in range(w) if col_has_content(x)), 0)
    right = next((x for x in range(w-1, -1, -1) if col_has_content(x)), w-1)
    top = max(0, top-pad); left = max(0, left-pad)
    bot = min(h, bot+pad); right = min(w, right+pad)
    im.crop((left, top, right, bot)).save(dst)
    print(f"{dst}: {right-left}x{bot-top}")

autocrop("/tmp/shots/dashboard-dark.png", "/home/kb/Desktop/projects/linux-lifepo4-bms-monitor/assets/dashboard-dark.png", (11, 13, 18))
autocrop("/tmp/shots/dashboard-light.png", "/home/kb/Desktop/projects/linux-lifepo4-bms-monitor/assets/dashboard-light.png", (248, 250, 252))
