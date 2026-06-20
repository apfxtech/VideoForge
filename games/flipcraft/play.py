import os
import re
import sys
import threading
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from flipperzero_protobuf.flipper_proto import FlipperProto

WIDTH, HEIGHT = 128, 64
SCALE = 8
VIDEO_W, VIDEO_H = 1080, 1920
FPS = 30

BG = (0, 130, 255)
WHITE = (25, 128, 254)
BLACK = (8, 8, 8)

SCREEN_W, SCREEN_H = WIDTH * SCALE, HEIGHT * SCALE
SX = (VIDEO_W - SCREEN_W) // 2
SY = (VIDEO_H - SCREEN_H) // 2

FLIPPER_SCALE = 4

TEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".sources", "flipcraft", "assets", "textures.inc")
BLOCK = 80
TEX_GRASS = 2
TEX_DIRT = 3
TEX_STONE = 4
TEX_COBBLE = 5
TEX_LOG = 7
TEX_LEAVES = 8
TEX_PLANK = 9
TEX_TABLE = 15
TEX_FURNACE = 19

FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"
TITLE = "FLIPCRAFT"
CAPTION = "AVAILABLE ON UNLSHD-090E FIRMWARE"


def decode_frame_to_bgr(frame_data):
    pages = np.frombuffer(frame_data, dtype=np.uint8).reshape(HEIGHT // 8, WIDTH)
    bits = np.unpackbits(pages, axis=0).reshape(8, 8, WIDTH)[:, ::-1, :]
    mono = bits.reshape(HEIGHT, WIDTH)
    bgr = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    bgr[mono == 1] = BLACK
    bgr[mono == 0] = WHITE
    return bgr


def load_flipper():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flipper.png")
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    b = img[:, :, 0].astype(np.int32)
    g = img[:, :, 1].astype(np.int32)
    r = img[:, :, 2].astype(np.int32)
    a = img[:, :, 3] if img.shape[2] == 4 else np.full(img.shape[:2], 255, np.int32)
    green = (g > 200) & (r < 60) & (b < 60)
    mask = (a >= 128) & ~green
    bit = (r + g + b) > 384
    h, w = mask.shape
    rgb = np.empty((h, w, 3), dtype=np.uint8)
    rgb[bit] = BG
    rgb[~bit] = BLACK
    big_rgb = cv2.resize(rgb, (w * FLIPPER_SCALE, h * FLIPPER_SCALE), interpolation=cv2.INTER_NEAREST)
    big_mask = cv2.resize(mask.astype(np.uint8), (w * FLIPPER_SCALE, h * FLIPPER_SCALE), interpolation=cv2.INTER_NEAREST).astype(bool)
    return big_rgb, big_mask


def load_textures():
    text = open(TEX_PATH, encoding="utf-8", errors="ignore").read()
    rows = re.findall(r"\{([01,\s]+)\}", text)
    tex = np.zeros((len(rows), 8, 8), dtype=np.uint8)
    for i, row in enumerate(rows):
        vals = [int(v) for v in row.split(",") if v.strip() != ""]
        tex[i] = np.array(vals[:64], dtype=np.uint8).reshape(8, 8)
    return tex


def paste(canvas, img, x, y):
    h, w = img.shape[:2]
    H, W = canvas.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    canvas[y0:y1, x0:x1] = img[y0 - y:y1 - y, x0 - x:x1 - x]


def block_tile(tex, idx, px):
    cell = tex[idx]
    rgb = np.empty((8, 8, 3), dtype=np.uint8)
    rgb[cell == 1] = BLACK
    rgb[cell == 0] = BG
    big = cv2.resize(rgb, (px, px), interpolation=cv2.INTER_NEAREST)
    cv2.rectangle(big, (0, 0), (px - 1, px - 1), BLACK, 3)
    return big


def draw_block(canvas, tex, idx, x, y, px):
    paste(canvas, block_tile(tex, idx, px), x, y)


def draw_tree(canvas, tex, x_left, y_bottom, px):
    trunk_x = x_left + px
    draw_block(canvas, tex, TEX_LOG, trunk_x, y_bottom - px, px)
    draw_block(canvas, tex, TEX_LOG, trunk_x, y_bottom - 2 * px, px)
    for cx in range(3):
        for cy in range(2):
            draw_block(canvas, tex, TEX_LEAVES, x_left + cx * px, y_bottom - (3 + cy) * px, px)


def text_mask(text, size):
    font = ImageFont.truetype(FONT_PATH, size)
    box = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
    img = Image.new("L", (box[2] - box[0] + 2, box[3] - box[1] + 2), 0)
    ImageDraw.Draw(img).text((1 - box[0], 1 - box[1]), text, font=font, fill=255)
    return (np.asarray(img) > 96).astype(np.uint8)


def draw_pixel_text(canvas, text, y, size, scale, max_w=VIDEO_W - 40):
    mask = text_mask(text, size)
    scale = max(1, min(scale, max_w // mask.shape[1]))
    big = cv2.resize(mask, (mask.shape[1] * scale, mask.shape[0] * scale), interpolation=cv2.INTER_NEAREST)
    border = max(2, scale)
    kernel = np.ones((2 * border + 1, 2 * border + 1), np.uint8)
    outline = cv2.dilate(big, kernel)
    h, w = big.shape
    H, W = canvas.shape[:2]
    x = (VIDEO_W - w) // 2
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    region = canvas[y0:y1, x0:x1]
    ob = outline[y0 - y:y1 - y, x0 - x:x1 - x]
    bb = big[y0 - y:y1 - y, x0 - x:x1 - x]
    region[ob > 0] = BLACK
    region[bb > 0] = BG


def build_background():
    tex = load_textures()
    canvas = np.empty((VIDEO_H, VIDEO_W, 3), dtype=np.uint8)
    canvas[:, :] = BG
    draw_pixel_text(canvas, TITLE, 70, 16, 11)

    px = BLOCK
    ground = SY - 110
    for i in range(VIDEO_W // px + 1):
        draw_block(canvas, tex, TEX_GRASS, i * px, ground, px)

    draw_tree(canvas, tex, 24, ground, px)
    draw_tree(canvas, tex, VIDEO_W - 24 - 3 * px, ground, px)

    cx = VIDEO_W // 2
    draw_block(canvas, tex, TEX_FURNACE, cx - px - 24, ground - px, px)
    draw_block(canvas, tex, TEX_TABLE, cx + 24, ground - px, px)

    flipper = load_flipper()
    if flipper is not None:
        rgb, mask = flipper
        fh, fw = mask.shape
        fy = SY + SCREEN_H + 80
        fx = (VIDEO_W - fw) // 2
        region = canvas[fy:fy + fh, fx:fx + fw]
        region[mask] = rgb[mask]

    draw_pixel_text(canvas, CAPTION, SY + SCREEN_H + 220, 12, 5)

    return canvas


def main():
    proto = FlipperProto()
    proto.rpc_device_info()
    proto.rpc_gui_start_screen_stream()

    background = build_background()
    writer = cv2.VideoWriter(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "play.mp4"),
        cv2.VideoWriter_fourcc(*"mp4v"),
        FPS,
        (VIDEO_W, VIDEO_H),
    )
    cv2.namedWindow("flipcraft", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("flipcraft", VIDEO_W // 3, VIDEO_H // 3)

    lock = threading.Lock()
    latest = {"data": None, "seq": 0}
    stop_event = threading.Event()

    def reader():
        seq = 0
        try:
            while not stop_event.is_set():
                msg = proto._rpc_read_any()
                if not hasattr(msg, "gui_screen_frame"):
                    continue
                frame = msg.gui_screen_frame.data
                if not frame or len(frame) != WIDTH * (HEIGHT // 8):
                    continue
                seq += 1
                with lock:
                    latest["data"] = bytes(frame)
                    latest["seq"] = seq
        except Exception as e:
            stop_event.set()
            print(f"Reader stopped: {e}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    last_seq = 0
    running = True
    try:
        while running and not stop_event.is_set():
            with lock:
                frame_data = latest["data"]
                seq = latest["seq"]
            if frame_data is None:
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    running = False
                time.sleep(0.001)
                continue
            last_seq = seq

            canvas = background.copy()
            screen = decode_frame_to_bgr(frame_data)
            big = cv2.resize(screen, (SCREEN_W, SCREEN_H), interpolation=cv2.INTER_NEAREST)
            canvas[SY:SY + SCREEN_H, SX:SX + SCREEN_W] = big

            writer.write(canvas)
            cv2.imshow("flipcraft", canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                running = False
            time.sleep(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        try:
            proto.rpc_gui_stop_screen_stream()
        except Exception:
            pass
        writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
