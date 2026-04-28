# Vifer_Stick_v1.pyw – polished, persistent floating sticker
# AI‑generated portions released under CC0‑1.0.
# See LICENSE file for full terms.

import tkinter as tk
from tkinter import filedialog, Menu, Toplevel, messagebox
from tkinter import ttk
from PIL import Image, ImageTk, ImageSequence, ImageDraw, ImageOps
import pystray
import threading
import sys
import subprocess
import ctypes
import json
from pathlib import Path

# Windows API for click‑through
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x80000
WS_EX_TRANSPARENT = 0x20

# Portable config file – always in the same folder as the executable / script
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "config.json"

class Sticker:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ViferStick")
        self.root.overrideredirect(True)

        self.TRANS_COLOR = '#010203'
        self.root.configure(bg=self.TRANS_COLOR)
        self.root.wm_attributes('-transparentcolor', self.TRANS_COLOR)
        self.root.wm_attributes('-topmost', False)
        self.root.wm_attributes('-alpha', 1.0)

        # State variables (loaded from config later)
        self.image_path = None
        self.scale_factor = 1.0
        self.rotation_angle = 0.0
        self.mirror = tk.BooleanVar(value=False)
        self.topmost = tk.BooleanVar(value=False)
        self.locked = tk.BooleanVar(value=False)
        self.opacity = 1.0

        # Image / animation
        self.original_frames = []
        self.frame_durations = []
        self.current_frame_index = 0
        self.img_tk = None
        self.is_animated = False
        self.anim_job = None

        self.label = tk.Label(self.root, bg=self.TRANS_COLOR, cursor="fleur")
        self.label.pack()

        self._drag_x = 0
        self._drag_y = 0
        self._bind_drag_events()

        self.slider_window = None

        self.ctx_menu = Menu(self.root, tearoff=0)
        self._build_context_menu()
        self.label.bind("<Button-3>", self.show_menu)

        self.tray_icon_image = self._make_tray_icon()
        self.tray = None
        self.tray_thread = None
        self._build_tray_menu()
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        # Load saved config (if any)
        self.load_config()

        # If no image was loaded (first launch or config missing image), prompt once
        if not self.original_frames:
            self.change_image()

    # ---------- tray icon ----------
    def _make_tray_icon(self):
        size = 32
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([4, 4, size-4, size-4], fill=(255, 255, 255, 255), outline=(0, 0, 0, 180))
        draw.polygon([(size-12, size-4), (size-4, size-4), (size-4, size-12)],
                     fill=(200, 200, 200, 255))
        draw.line([(size-12, size-4), (size-4, size-12)], fill=(0, 0, 0, 180), width=1)
        draw.text((9, 5), "V", fill=(0, 0, 0, 200))
        return img

    # ---------- config persistence ----------
    def save_config(self):
        """Save visual/position settings, preserving the existing image_path."""
        # Read current config to keep any manually set image_path
        current_data = {}
        if CONFIG_FILE.is_file():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    current_data = json.load(f)
            except Exception:
                pass

        # Preserve the image_path exactly as it was (empty or filled)
        preserved_image_path = current_data.get("image_path", "")

        data = {
            "image_path": preserved_image_path,   # <-- stays forever
            "scale_factor": self.scale_factor,
            "rotation_angle": self.rotation_angle % 360,
            "mirror": self.mirror.get(),
            "topmost": self.topmost.get(),
            "opacity": self.root.wm_attributes('-alpha'),
            "locked": self.locked.get(),
            "window_x": self.root.winfo_x(),
            "window_y": self.root.winfo_y()
        }
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def load_config(self):
        """Load saved settings. Creates default config with blank image_path if missing."""
        if not CONFIG_FILE.is_file():
            # First launch – write default config with empty image_path
            default_data = {
                "image_path": "",
                "scale_factor": 1.0,
                "rotation_angle": 0.0,
                "mirror": False,
                "topmost": False,
                "opacity": 1.0,
                "locked": False,
                "window_x": 200,
                "window_y": 200
            }
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(default_data, f, indent=2)
            except Exception:
                pass
            # No image to restore; init will handle the prompt later
            return

        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return

        # Restore image only if a valid image_path exists in the config
        img_path = data.get("image_path", "")
        if img_path and Path(img_path).is_file():
            self.set_image(img_path)
        # Otherwise: no image; the init check will trigger change_image() once

        # Restore visual settings
        self.scale_factor = data.get("scale_factor", 1.0)
        self.rotation_angle = data.get("rotation_angle", 0.0)
        self.mirror.set(data.get("mirror", False))
        self.topmost.set(data.get("topmost", False))
        self.root.wm_attributes('-topmost', self.topmost.get())
        self.opacity = data.get("opacity", 1.0)
        self.root.wm_attributes('-alpha', self.opacity)

        # Restore position (ensure on‑screen)
        x = data.get("window_x", 200)
        y = data.get("window_y", 200)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        if x > screen_w or y > screen_h or x < -500 or y < -500:
            x = (screen_w - 200) // 2
            y = (screen_h - 200) // 2
        self.root.geometry(f"+{x}+{y}")

        # Start unlocked for safety
        self.locked.set(False)

        self.update_display()

    # ---------- dynamic tray menu ----------
    def _build_tray_menu(self):
        self.tray_menu = pystray.Menu(
            pystray.MenuItem("Show (center)", self.show_sticker),
            pystray.MenuItem("Always on Top", self.tray_toggle_topmost,
                             checked=lambda item: self.topmost.get()),
            pystray.MenuItem("Lock", self.tray_toggle_lock,
                             checked=lambda item: self.locked.get()),
            pystray.MenuItem("Mirror", self.tray_toggle_mirror,
                             checked=lambda item: self.mirror.get()),
            pystray.MenuItem("Opacity…", self.open_opacity_dialog),
            pystray.MenuItem("Change image…", self.change_image),
            pystray.MenuItem("Resize…", self.open_resize_dialog),
            pystray.MenuItem("Rotation…", self.open_rotation_dialog),
            pystray.MenuItem("Reset all settings", self.reset_all),
            pystray.MenuItem("New sticker", self.spawn_sticker),
            pystray.MenuItem("Close", self.quit_app)
        )

    def refresh_tray_menu(self):
        self._build_tray_menu()
        if self.tray is not None:
            self.tray.menu = self.tray_menu
            if hasattr(self.tray, 'update_menu'):
                self.tray.update_menu()

    # ---------- context menu ----------
    def _build_context_menu(self):
        self.ctx_menu.delete(0, 'end')
        self.ctx_menu.add_command(label="Change image…", command=self.change_image)
        self.ctx_menu.add_command(label="Resize…", command=self.open_resize_dialog)
        self.ctx_menu.add_command(label="Opacity…", command=self.open_opacity_dialog)
        self.ctx_menu.add_command(label="Rotation…", command=self.open_rotation_dialog)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_checkbutton(label="Always on Top",
                                      variable=self.topmost,
                                      command=self.toggle_topmost)
        self.ctx_menu.add_checkbutton(label="Mirror",
                                      variable=self.mirror,
                                      command=self.update_display)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Lock", command=self.lock_sticker)
        self.ctx_menu.add_command(label="New sticker", command=self.spawn_sticker)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Close", command=self.quit_app)

    def show_menu(self, event):
        if not self.locked.get():
            self.ctx_menu.tk_popup(event.x_root, event.y_root)

    # ---------- lock / click‑through ----------
    def lock_sticker(self):
        if self.locked.get():
            return
        self.locked.set(True)
        self._set_click_through(True)
        self._unbind_drag_events()
        self.label.unbind("<Button-3>")
        self.refresh_tray_menu()
        self.save_config()

    def unlock_sticker(self):
        if not self.locked.get():
            return
        self.locked.set(False)
        self._set_click_through(False)
        self._bind_drag_events()
        self.label.bind("<Button-3>", self.show_menu)
        self.refresh_tray_menu()
        self.save_config()

    def tray_toggle_lock(self):
        if self.locked.get():
            self.unlock_sticker()
        else:
            self.lock_sticker()

    def _set_click_through(self, enable: bool):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enable:
                style |= WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0002 | 0x0001)
        except Exception:
            pass

    def _bind_drag_events(self):
        self.label.bind("<Button-1>", self.start_drag)
        self.label.bind("<B1-Motion>", self.on_drag)
        self.root.bind("<Button-1>", self.start_drag)
        self.root.bind("<B1-Motion>", self.on_drag)

    def _unbind_drag_events(self):
        self.label.unbind("<Button-1>")
        self.label.unbind("<B1-Motion>")
        self.root.unbind("<Button-1>")
        self.root.unbind("<B1-Motion>")

    # ---------- loading media ----------
    def set_image(self, path):
        self.stop_animation()
        self.image_path = path
        img = Image.open(path)
        self.original_frames = []
        self.frame_durations = []

        if getattr(img, "is_animated", False):
            self.is_animated = True
            for frame in ImageSequence.Iterator(img):
                rgba_frame = frame.convert("RGBA")
                self.original_frames.append(rgba_frame)
                duration = frame.info.get('duration', 100)
                self.frame_durations.append(duration)
        else:
            self.is_animated = False
            self.original_frames.append(img.convert("RGBA"))
            self.frame_durations.append(0)

        self.current_frame_index = 0
        self.update_display()
        if self.is_animated:
            self.start_animation()
        # Do NOT auto‑save the image path – keeping portability and manual config

    def update_display(self):
        if not self.original_frames:
            return
        frame = self.original_frames[self.current_frame_index]
        if self.mirror.get():
            frame = ImageOps.mirror(frame)
        w, h = frame.size
        new_size = (int(w * self.scale_factor), int(h * self.scale_factor))
        resized = frame.resize(new_size, Image.Resampling.LANCZOS)

        if self.rotation_angle % 360 != 0:
            rotated = resized.rotate(self.rotation_angle, expand=True, resample=Image.Resampling.BICUBIC)
        else:
            rotated = resized

        self.img_tk = ImageTk.PhotoImage(rotated)
        self.label.configure(image=self.img_tk)
        new_w, new_h = rotated.size
        if self.root.winfo_width() == 1:
            self.root.geometry(f"{new_w}x{new_h}")
        else:
            self.root.geometry(f"{new_w}x{new_h}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def start_animation(self):
        self.stop_animation()
        if not self.is_animated or len(self.original_frames) <= 1:
            return
        delay = self.frame_durations[self.current_frame_index]
        self.current_frame_index = (self.current_frame_index + 1) % len(self.original_frames)
        self.update_display()
        self.anim_job = self.root.after(delay, self.start_animation)

    def stop_animation(self):
        if self.anim_job is not None:
            self.root.after_cancel(self.anim_job)
            self.anim_job = None

    # ---------- dragging ----------
    def start_drag(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def on_drag(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")
        self.save_config()

    # ---------- topmost ----------
    def toggle_topmost(self, *args):
        self.root.wm_attributes('-topmost', self.topmost.get())
        if self.slider_window and self.slider_window.winfo_exists():
            self.slider_window.wm_attributes('-topmost', self.topmost.get())
        self.save_config()

    def tray_toggle_topmost(self):
        self.topmost.set(not self.topmost.get())
        self.toggle_topmost()

    # ---------- mirror ----------
    def tray_toggle_mirror(self):
        self.mirror.set(not self.mirror.get())
        self.update_display()
        self.save_config()

    # ---------- change image ----------
    def change_image(self):
        path = filedialog.askopenfilename(
            title="Select media",
            filetypes=[
                ("All supported", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("PNG images", "*.png"),
                ("JPEG images", "*.jpg *.jpeg"),
                ("GIF images", "*.gif"),
                ("BMP images", "*.bmp"),
                ("WebP images", "*.webp"),
                ("All files", "*.*")
            ]
        )
        if path:
            self.set_image(path)

    # ---------- modern slider windows ----------
    def _center_window(self, win):
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        ww = win.winfo_reqwidth()
        wh = win.winfo_reqheight()
        x = (sw - ww) // 2
        y = (sh - wh) // 2
        win.geometry(f"+{x}+{y}")

    def _make_slider_window(self, title, from_val, to_val, init_val, apply_cmd, reset_cmd=lambda: None):
        """Create a clean ttk‑themed slider window with an editable value entry."""
        if self.slider_window and self.slider_window.winfo_exists():
            self.slider_window.destroy()
        win = Toplevel(self.root)
        win.title(title)
        win.resizable(False, False)
        win.configure(bg='#f0f0f0')
        win.wm_attributes('-topmost', self.topmost.get())

        style = ttk.Style(win)
        try:
            style.theme_use('vista')
        except tk.TclError:
            try:
                style.theme_use('clam')
            except tk.TclError:
                pass

        value_var = tk.StringVar(value=f"{init_val:.0f}")

        vcmd = (win.register(self._validate_int), '%P')
        entry = ttk.Entry(win, textvariable=value_var, font=("Segoe UI", 14, "bold"),
                          justify='center', width=5, validate='key', validatecommand=vcmd)
        entry.pack(pady=(15, 5))

        scale = ttk.Scale(win, from_=from_val, to=to_val, orient=tk.HORIZONTAL,
                          length=320, command=lambda v: value_var.set(f"{float(v):.0f}"))
        scale.set(init_val)
        scale.pack(padx=25, pady=10)

        def sync_from_entry(event=None):
            try:
                val = float(value_var.get())
                val = max(from_val, min(val, to_val))
                scale.set(val)
                value_var.set(f"{val:.0f}")
            except ValueError:
                value_var.set(f"{init_val:.0f}")

        entry.bind('<Return>', sync_from_entry)
        entry.bind('<FocusOut>', sync_from_entry)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=(5, 15))

        def apply():
            sync_from_entry()
            val = scale.get()
            apply_cmd(val)

        ttk.Button(btn_frame, text="Apply", command=apply).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Reset", command=lambda: [scale.set(init_val), value_var.set(f"{init_val:.0f}"), reset_cmd()]).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Close", command=win.destroy).pack(side=tk.LEFT, padx=5)

        self._center_window(win)
        self.slider_window = win

    @staticmethod
    def _validate_int(P):
        if P == "" or P == "-":
            return True
        if P.startswith("-"):
            return P[1:].isdigit()
        return P.isdigit()

    # ---------- slider dialogs ----------
    def open_resize_dialog(self):
        if not self.original_frames:
            return
        current_pct = self.scale_factor * 100

        def apply(val):
            self.scale_factor = val / 100.0
            self.update_display()
            self.save_config()

        def reset():
            self.scale_factor = 1.0
            self.update_display()
            self.save_config()

        self._make_slider_window("Resize (%)", 10, 500, current_pct, apply, reset)

    def open_opacity_dialog(self):
        current_alpha = self.root.wm_attributes('-alpha') * 100

        def apply(val):
            self.root.wm_attributes('-alpha', val / 100.0)
            self.save_config()

        def reset():
            self.root.wm_attributes('-alpha', 1.0)
            self.save_config()

        self._make_slider_window("Opacity (%)", 10, 100, current_alpha, apply, reset)

    def open_rotation_dialog(self):
        if not self.original_frames:
            return
        current_angle = self.rotation_angle % 360

        def apply(val):
            self.rotation_angle = val
            self.update_display()
            self.save_config()

        def reset():
            self.rotation_angle = 0.0
            self.update_display()
            self.save_config()

        self._make_slider_window("Rotation (°)", 0, 359, current_angle, apply, reset)

    # ---------- multi‑sticker ----------
    def spawn_sticker(self):
        if getattr(sys, 'frozen', False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, __file__])

    # ---------- reset all ----------
    def reset_all(self):
        self.scale_factor = 1.0
        self.rotation_angle = 0.0
        self.mirror.set(False)
        self.root.wm_attributes('-alpha', 1.0)
        self.root.wm_attributes('-topmost', False)
        self.topmost.set(False)
        self.update_display()
        self.save_config()
        messagebox.showinfo("ViferStick", "All settings have been reset to defaults.")

    # ---------- tray ----------
    def show_sticker(self):
        self.root.deiconify()
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        w = self.root.winfo_width() or 200
        h = self.root.winfo_height() or 200
        x = (screen_w - w) // 2
        y = (screen_h - h) // 2
        self.root.geometry(f"+{x}+{y}")
        self.root.lift()

    # ---------- quit ----------
    def quit_app(self):
        self.save_config()
        self.stop_animation()
        self._set_click_through(False)
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()
        sys.exit(0)

    # ---------- run ----------
    def run(self):
        self._build_tray_menu()
        self.tray = pystray.Icon("ViferStick", self.tray_icon_image, "ViferStick", self.tray_menu)
        self.tray_thread = threading.Thread(target=self.tray.run, daemon=True)
        self.tray_thread.start()
        self.root.mainloop()

if __name__ == "__main__":
    app = Sticker()
    app.run()