import os
import threading
import time

import cv2
import numpy as np
from flipperzero_protobuf.flipper_proto import FlipperProto

WIDTH, HEIGHT = 128, 64
VIDEO_W, VIDEO_H = 1280, 720
FPS = 30

BG = (0, 130, 255)
WHITE = (25, 128, 254)
BLACK = (8, 8, 8)

SCALE = min(VIDEO_W // WIDTH, VIDEO_H // HEIGHT) -1
SCREEN_W, SCREEN_H = WIDTH * SCALE, HEIGHT * SCALE
SX = (VIDEO_W - SCREEN_W) // 2
SY = (VIDEO_H - SCREEN_H) // 2


def decode_frame_to_bgr(frame_data):
    pages = np.frombuffer(frame_data, dtype=np.uint8).reshape(HEIGHT // 8, WIDTH)
    bits = np.unpackbits(pages, axis=0).reshape(8, 8, WIDTH)[:, ::-1, :]
    mono = bits.reshape(HEIGHT, WIDTH)
    bgr = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    bgr[mono == 1] = BLACK
    bgr[mono == 0] = WHITE
    return bgr


def main():
    proto = FlipperProto()
    proto.rpc_device_info()
    proto.rpc_gui_start_screen_stream()

    writer = cv2.VideoWriter(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "screen.mp4"),
        cv2.VideoWriter_fourcc(*"mp4v"),
        FPS,
        (VIDEO_W, VIDEO_H),
    )
    cv2.namedWindow("flipcraft-screen", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("flipcraft-screen", VIDEO_W // 2, VIDEO_H // 2)

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

    running = True
    try:
        while running and not stop_event.is_set():
            with lock:
                frame_data = latest["data"]
            if frame_data is None:
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    running = False
                time.sleep(0.001)
                continue

            canvas = np.empty((VIDEO_H, VIDEO_W, 3), dtype=np.uint8)
            canvas[:, :] = BG
            screen = decode_frame_to_bgr(frame_data)
            big = cv2.resize(screen, (SCREEN_W, SCREEN_H), interpolation=cv2.INTER_NEAREST)
            canvas[SY:SY + SCREEN_H, SX:SX + SCREEN_W] = big

            writer.write(canvas)
            cv2.imshow("flipcraft-screen", canvas)
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
