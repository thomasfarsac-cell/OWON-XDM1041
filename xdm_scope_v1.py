
import sys
import time
import csv
import threading
import queue
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import serial
import serial.tools.list_ports

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ---------------------------------
# SCPI / Serial helpers
# ---------------------------------

DEFAULT_BAUD = 115200
MIN_POLL_HZ = 0.5

def list_serial_ports():
    return [p.device for p in serial.tools.list_ports.comports()]

def open_serial(port: str, baud: int = DEFAULT_BAUD):
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.2,
        write_timeout=0.2
    )

def scpi_write(ser: serial.Serial, cmd: str):
    if not ser or not ser.is_open:
        return
    try:
        ser.write((cmd + "\r").encode())
    except Exception:
        pass

def scpi_query(ser: serial.Serial, cmd: str) -> str:
    if not ser or not ser.is_open:
        return ""
    try:
        ser.reset_output_buffer()
        ser.reset_input_buffer()
        ser.write((cmd + "\r").encode())
        line = ser.readline().decode(errors="ignore").strip()
        return line
    except Exception:
        return ""

def try_detect_idn(ser: serial.Serial) -> str:
    return scpi_query(ser, "*IDN?")

def looks_like_xdm(idn: str) -> bool:
    if not idn:
        return False
    token = idn.upper()
    return ("OWON" in token) or ("XDM" in token)

def query_mode(ser: serial.Serial) -> str:
    resp = scpi_query(ser, "FUNC?")
    return resp.strip('"')

def query_value(ser: serial.Serial) -> str:
    return scpi_query(ser, "MEAS?")

def query_range(ser: serial.Serial) -> str:
    # best effort
    for cmd in (
        "CONF?",
        "CONF:VOLT?",
        "CONF:CURR?",
        "CONF:RES?",
        "CONF:CAP?",
        "CONF:FREQ?",
        "RANG?",
        "RANGE?",
        "AUTO?"
    ):
        r = scpi_query(ser, cmd)
        if r:
            return r
    return "Auto / —"

# ---------------------------------
# Formatting helpers
# ---------------------------------

SI_PREFIXES = [
    (1e-12, "p"),
    (1e-9,  "n"),
    (1e-6,  "µ"),
    (1e-3,  "m"),
    (1,     ""),
    (1e3,   "k"),
    (1e6,   "M"),
    (1e9,   "G"),
]

MODE_UNIT = {
    "VOLT": "V",
    "VOLT AC": "V",
    "CURR": "A",
    "CURR AC": "A",
    "CURR DC": "A",
    "CURR AC": "A",
    "RES": "Ω",
    "FRES": "Ω",
    "CONT": "Ω",
    "DIODE": "V",
    "DIOD": "V",
    "CAP": "F",
    "FREQ": "Hz",
    "PER": "s",
    "TEMP": "°C",
}

# modes qui ne doivent pas descendre <0 visuellement
NON_NEGATIVE_EXCEPTIONS = {"VOLT", "VOLT AC", "CURR", "CURR DC", "CURR AC", "CURR AC", "CURR AC", "CURR AC"}
# On fait simple : autoriser négatif uniquement pour tension et courant
def allow_negative(mode: str) -> bool:
    if mode is None: 
        return True
    u = mode.upper()
    if "VOLT" in u or "CURR" in u:
        return True
    return False

def parse_float(text: str):
    try:
        return float(text)
    except Exception:
        return None

def humanize_value(val: float, unit: str) -> str:
    if val is None:
        return f"-- {unit}".strip()

    abs_v = abs(val)
    chosen = SI_PREFIXES[4]  # default (1, "")
    for base, prefix in SI_PREFIXES:
        if abs_v >= base or (abs_v == 0 and base == 1):
            chosen = (base, prefix)
    base, prefix = chosen
    scaled = val / base if base != 0 else val

    if abs(scaled) >= 100:
        s = f"{scaled:.0f}"
    elif abs(scaled) >= 10:
        s = f"{scaled:.1f}"
    else:
        s = f"{scaled:.3f}".rstrip("0").rstrip(".")

    return f"{s} {prefix}{unit}".strip()

def mode_to_unit(mode: str) -> str:
    if not mode:
        return ""
    # try exact match
    if mode in MODE_UNIT:
        return MODE_UNIT[mode]
    # attempt partial matches like "VOLT DC" => "VOLT"
    high = mode.upper()
    for key, u in MODE_UNIT.items():
        if key in high:
            return u
    return ""

# ---------------------------------
# Go/No-Go Window
# ---------------------------------

class GoNoGoWindow(tk.Toplevel):
    def __init__(self, master, get_current_unit):
        super().__init__(master)
        self.title("🧪 Go / No-Go")
        self.geometry("420x360")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        self.get_current_unit = get_current_unit

        self.test_name = tk.StringVar(value="Test universel")
        self.nominal_var = tk.StringVar(value="0")
        self.min_var = tk.StringVar(value="0")
        self.max_var = tk.StringVar(value="0")
        self.custom_tol_var = tk.StringVar(value="2.0")  # %

        self.read_value_var = tk.StringVar(value="--")
        self.delta_var = tk.StringVar(value="Δ% : —")
        self.verdict_var = tk.StringVar(value="—")
        self._verdict_label = None

        self._build_ui()

    def _build_ui(self):
        frm = tk.Frame(self)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        # Nom test
        tk.Label(frm, text="Nom du test :").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        tk.Entry(frm, textvariable=self.test_name, width=28).grid(row=0, column=1, columnspan=3, sticky="w", padx=4, pady=4)

        # Nominal / Min / Max
        tk.Label(frm, text="Nominal :").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        tk.Entry(frm, textvariable=self.nominal_var, width=10).grid(row=1, column=1, sticky="w", padx=4, pady=4)
        tk.Label(frm, text="Min :").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        tk.Entry(frm, textvariable=self.min_var, width=10).grid(row=2, column=1, sticky="w", padx=4, pady=4)
        tk.Label(frm, text="Max :").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        tk.Entry(frm, textvariable=self.max_var, width=10).grid(row=3, column=1, sticky="w", padx=4, pady=4)

        # Presets tolérance
        tk.Label(frm, text="Tolérance :").grid(row=1, column=2, sticky="e", padx=(12,4))
        ttk.Button(frm, text="±0.5%", width=8, command=lambda: self.apply_tol(0.5)).grid(row=1, column=3, padx=2)
        ttk.Button(frm, text="±1%",   width=8, command=lambda: self.apply_tol(1.0)).grid(row=2, column=3, padx=2)
        ttk.Button(frm, text="±5%",   width=8, command=lambda: self.apply_tol(5.0)).grid(row=3, column=3, padx=2)

        tk.Label(frm, text="Custom % :").grid(row=4, column=2, sticky="e", padx=(12,4))
        tk.Entry(frm, textvariable=self.custom_tol_var, width=8).grid(row=4, column=3, sticky="w", padx=2)
        ttk.Button(frm, text="Appliquer", command=self.apply_custom_tol).grid(row=4, column=1, sticky="w", padx=2)

        sep = ttk.Separator(frm, orient="horizontal")
        sep.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(6,6))

        tk.Label(frm, text="Mesure :").grid(row=6, column=0, sticky="e", padx=4, pady=2)
        self.lbl_value = tk.Label(frm, textvariable=self.read_value_var, font=("Consolas", 16, "bold"), fg="red")
        self.lbl_value.grid(row=6, column=1, columnspan=3, sticky="w")

        tk.Label(frm, textvariable=self.delta_var, font=("Consolas", 12)).grid(row=7, column=1, columnspan=3, sticky="w", padx=4)

        tk.Label(frm, text="Résultat :").grid(row=8, column=0, sticky="e", padx=4, pady=6)
        self._verdict_label = tk.Label(frm, textvariable=self.verdict_var, font=("Consolas", 18, "bold"))
        self._verdict_label.grid(row=8, column=1, columnspan=3, sticky="w", padx=4, pady=6)

        tk.Button(frm, text="Fermer", command=self.destroy).grid(row=9, column=3, sticky="e", pady=(10,0))

        for i in range(4):
            frm.grid_columnconfigure(i, weight=1)

    def apply_tol(self, tol_percent: float):
        try:
            nominal = float(self.nominal_var.get().replace(",", "."))
        except Exception:
            messagebox.showwarning("Attention", "Nominal invalide.")
            return
        vmin = nominal * (1 - tol_percent/100.0)
        vmax = nominal * (1 + tol_percent/100.0)
        self.min_var.set(f"{vmin:.6g}")
        self.max_var.set(f"{vmax:.6g}")

    def apply_custom_tol(self):
        try:
            tol = float(self.custom_tol_var.get().replace(",", "."))
        except Exception:
            messagebox.showwarning("Attention", "Tolérance invalide.")
            return
        self.apply_tol(tol)

    def update_with_measure(self, value_float: float):
        unit = self.get_current_unit()
        human = humanize_value(value_float, unit) if value_float is not None else f"-- {unit}"
        self.read_value_var.set(human)

        # Parse thresholds
        try:
            nominal = float(self.nominal_var.get().replace(",", "."))
        except Exception:
            nominal = None
        try:
            vmin = float(self.min_var.get().replace(",", "."))
        except Exception:
            vmin = None
        try:
            vmax = float(self.max_var.get().replace(",", "."))
        except Exception:
            vmax = None

        # Deviation
        if nominal is None or value_float is None or nominal == 0:
            self.delta_var.set("Δ% : —")
        else:
            delta = (value_float - nominal) / nominal * 100.0
            self.delta_var.set(f"Δ% : {delta:+.2f} %")

        # Verdict
        verdict = "—"
        color = "black"
        if value_float is not None and vmin is not None and vmax is not None:
            if vmin <= value_float <= vmax:
                verdict = "GO"
                color = "green"
            else:
                verdict = "NO GO"
                color = "red"
        self.verdict_var.set(verdict)
        if self._verdict_label is not None:
            self._verdict_label.config(fg=color)

# ---------------------------------
# Mini Display Window
# ---------------------------------

class MiniDisplayWindow(tk.Toplevel):
    TRANSP_BG = "#858585"  # same idea as you picked

    def __init__(self, master, get_mode, get_range, get_value_string):
        super().__init__(master)
        self.title("🪟 Affichage minimal")
        self.geometry("260x240")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        self.get_mode = get_mode
        self.get_range = get_range
        self.get_value_string = get_value_string

        self.transparent_on = tk.BooleanVar(value=False)

        self._build_ui()
        self._tick()

    def _build_ui(self):
        self.configure(bg=self.TRANSP_BG)

        frm = tk.Frame(self, padx=10, pady=10, bg=self.TRANSP_BG, bd=0, highlightthickness=0)
        frm.pack(fill="both", expand=True)

        self.lbl_mode = tk.Label(frm, text="Mode : —", font=("Consolas", 22, "bold"), bg=self.TRANSP_BG)
        self.lbl_mode.pack(anchor="w")
        self.lbl_range = tk.Label(frm, text="Gamme : —", font=("Consolas", 22, "bold"), bg=self.TRANSP_BG)
        self.lbl_range.pack(anchor="w", pady=(0,8))

        self.lbl_value = tk.Label(frm, text="--", fg="red", font=("Consolas", 28, "bold"), bg=self.TRANSP_BG)
        self.lbl_value.pack(anchor="w", pady=(0,8))

        chk = tk.Checkbutton(
            frm,
            text="Fond transparent",
            variable=self.transparent_on,
            command=self._apply_transparency,
            bg=self.TRANSP_BG,
            font=("Consolas", 12)
        )
        chk.pack(anchor="w", pady=(10,0))

    def _apply_transparency(self):
        try:
            if self.transparent_on.get():
                self.attributes("-transparentcolor", self.TRANSP_BG)
            else:
                # remove transparentcolor key by setting empty
                self.attributes("-transparentcolor", "")
        except Exception:
            # not supported on this platform -> ignore
            pass

    def _tick(self):
        try:
            mode = self.get_mode()
            rng = self.get_range()
            val = self.get_value_string()
            self.lbl_mode.config(text=f"Mode : {mode if mode else '—'}")
            self.lbl_range.config(text=f"Gamme : {rng if rng else '—'}")
            self.lbl_value.config(text=val if val else "--")
        except Exception:
            pass
        self.after(200, self._tick)

# ---------------------------------
# Main App
# ---------------------------------

class XDMScope(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mini Oscillo-Multimètre (OWON XDM) — by Thomas")
        self.geometry("1200x820")
        self.minsize(1000, 720)

        # ---- runtime state ----
        self.ser = None
        self.read_thread = None
        self.read_stop = threading.Event()

        self.duration = tk.DoubleVar(value=30.0)         # seconds window
        self.duration_entry_var = tk.StringVar(value="30")
        self.poll_hz_var = tk.StringVar(value="2")       # default 2 Hz
        self.mode_var = tk.StringVar(value="—")
        self.range_var = tk.StringVar(value="Auto / —")
        self.value_var = tk.StringVar(value="--")
        self.idn_var = tk.StringVar(value="Non connecté")
        self.max_plot_var = tk.StringVar(value="")       # optional Y max

        self.last_mode = None

        # data arrays
        self.data_q = queue.Queue()
        self.data = []       # list of (t, val)
        self.t0 = time.time()

        # markers
        self.markerA = None
        self.markerB = None
        self.stats_label_var = tk.StringVar(value="A–B stats : n=0  min=—  max=—  mean=—")
        self.lbl_stats = None
        self.lbl_ab = None

        # extra windows
        self.gonogo_win = None
        self.mini_win = None

        # mode button highlight tracking
        self.mode_buttons = {}
        self.rate_buttons = {}
        self.active_mode_btn = None
        self.active_rate_btn = None

        self._build_ui()
        self._schedule_ui_update()

    # ---------------------------------
    # UI BUILD
    # ---------------------------------

    def _build_ui(self):
        # ----------- TOP ROW 1 : Connexion / Acquisition / Paramètres courts -----------
        top1 = tk.Frame(self); top1.pack(fill="x", padx=10, pady=(8,4))

        # Connexion Série
        conn = tk.LabelFrame(top1, text="Connexion Série")
        conn.pack(side="left", padx=(0,10), pady=(0,4))

        tk.Label(conn, text="Port :").grid(row=0, column=0, padx=4, pady=2, sticky="e")
        ports = list_serial_ports()
        self.port_var = tk.StringVar(value=ports[0] if ports else "")
        self.port_cb = ttk.Combobox(conn, textvariable=self.port_var, values=ports, width=10)
        self.port_cb.grid(row=0, column=1, padx=2, pady=2)

        tk.Label(conn, text="Vitesse :").grid(row=0, column=2, padx=4, pady=2, sticky="e")
        self.baud_var = tk.StringVar(value=str(DEFAULT_BAUD))
        self.baud_cb = ttk.Combobox(conn, textvariable=self.baud_var,
                                    values=["9600","19200","38400","57600","115200"],
                                    width=10)
        self.baud_cb.grid(row=0, column=3, padx=2, pady=2)

        ttk.Button(conn, text="Connecter", command=self.connect).grid(row=0, column=4, padx=4)
        ttk.Button(conn, text="Auto", command=self.auto_connect).grid(row=0, column=5, padx=2)
        ttk.Button(conn, text="Fermer", command=self.disconnect).grid(row=0, column=6, padx=4)

        # Acquisition (Start / Pause / Clear)
        acq = tk.LabelFrame(top1, text="Acquisition")
        acq.pack(side="left", padx=(0,10), pady=(0,4))

        self.btn_start = ttk.Button(acq, text="▶️ Start", command=self.toggle_run)
        self.btn_start.grid(row=0, column=0, padx=4)

        ttk.Button(acq, text="🧹 Clear", command=self.clear_data).grid(row=0, column=1, padx=4)

        # Paramètres (Polling / Durée / Limite Y)
        params = tk.LabelFrame(top1, text="Paramètres")
        params.pack(side="left", padx=(0,10), pady=(0,4))

        tk.Label(params, text="Polling (Hz):").grid(row=0, column=0, padx=(6,2))
        self.poll_entry = ttk.Entry(params, textvariable=self.poll_hz_var, width=6)
        self.poll_entry.grid(row=0, column=1, padx=(0,8))

        tk.Label(params, text="Durée (s):").grid(row=0, column=2, padx=(4,2))
        self.duration_entry = ttk.Entry(params, textvariable=self.duration_entry_var, width=6)
        self.duration_entry.grid(row=0, column=3, padx=(0,8))
        ttk.Button(params, text="Appliquer", command=self.apply_duration_from_entry).grid(row=0, column=4, padx=(0,8))

        tk.Label(params, text="Limite Y max:").grid(row=0, column=5, padx=(4,2))
        self.max_plot_entry = ttk.Entry(params, textvariable=self.max_plot_var, width=10)
        self.max_plot_entry.grid(row=0, column=6, padx=(0,8))

        # ----------- TOP ROW 2 : Modes / Rate / Tools -----------
        top2 = tk.Frame(self); top2.pack(fill="x", padx=10, pady=(0,4))

        # Modes (2 lignes)
        modes_frame = tk.LabelFrame(top2, text="Modes")
        modes_frame.pack(side="left", padx=(0,10), pady=(0,4))

        row1 = tk.Frame(modes_frame); row1.pack()
        row2 = tk.Frame(modes_frame); row2.pack()

        mode_list_row1 = ["VOLT DC","VOLT AC","CURR DC","CURR AC","RES"]
        mode_list_row2 = ["CONT","CAP","DIOD","FREQ","TEMP"]

        for mlabel in mode_list_row1:
            b = tk.Button(row1, text=mlabel, width=10,
                          command=lambda x=mlabel: self._set_mode_scpi(x))
            b.pack(side="left", padx=2, pady=2)
            self.mode_buttons[mlabel] = b

        for mlabel in mode_list_row2:
            b = tk.Button(row2, text=mlabel, width=10,
                          command=lambda x=mlabel: self._set_mode_scpi(x))
            b.pack(side="left", padx=2, pady=2)
            self.mode_buttons[mlabel] = b

        # Rate buttons
        rate_frame = tk.LabelFrame(top2, text="Vitesse")
        rate_frame.pack(side="left", padx=(0,10), pady=(0,4))

        for rate_label in ["S","M","F"]:
            rb = tk.Button(rate_frame, text=rate_label, width=4,
                           command=lambda r=rate_label: self._set_rate_scpi(r))
            rb.pack(side="left", padx=2, pady=2)
            self.rate_buttons[rate_label] = rb

        # Tools frame (Go/No-Go, Mini, CSV, PNG)
        tools_frame = tk.LabelFrame(top2, text="Tools")
        tools_frame.pack(side="left", padx=(0,10), pady=(0,4))

        ttk.Button(tools_frame, text="🧪 Go/No-Go", command=self.open_gonogo).pack(side="left", padx=4, pady=2)
        ttk.Button(tools_frame, text="🪟 Mini", command=self.open_mini).pack(side="left", padx=4, pady=2)
        ttk.Button(tools_frame, text="💾 CSV", command=self.save_csv).pack(side="left", padx=4, pady=2)
        ttk.Button(tools_frame, text="📸 PNG", command=self.save_png).pack(side="left", padx=4, pady=2)

        # ----------- Info row (mode / range / idn) -----------
        info = tk.Frame(self); info.pack(fill="x", padx=12, pady=(2,6))
        self.lbl_mode = tk.Label(info, text="Mode : —", font=("Consolas", 11))
        self.lbl_mode.pack(side="left", padx=8)
        self.lbl_range = tk.Label(info, text="Gamme : Auto / —", font=("Consolas", 11))
        self.lbl_range.pack(side="left", padx=18)
        tk.Label(info, textvariable=self.idn_var, font=("Consolas", 10), fg="#666").pack(side="right", padx=8)

        # ----------- Big value -----------
        big = tk.Frame(self); big.pack(fill="x", padx=12, pady=(4,0))
        self.big_value = tk.Label(big, textvariable=self.value_var, fg="red",
                                  font=("Consolas", 42, "bold"))
        self.big_value.pack(anchor="w", padx=6)

        # ----------- Matplotlib plot -----------
        self.fig, self.ax = plt.subplots(1, 1, figsize=(10,5), dpi=100)
        self.ax.set_title("Mesure en temps réel")
        self.ax.set_xlabel("Temps (s)")
        self.ax.set_ylabel("Valeur")
        self.line, = self.ax.plot([], [], lw=1.5)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(0,10))

        # mouse info / overlay
        self._mouse_info = self.ax.annotate(
            "", xy=(0,0), xytext=(10,10), textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w", alpha=0.7), fontsize=9, color="#333"
        )
        self._mouse_info.set_visible(False)

        # markers & stats row
        mk = tk.Frame(self); mk.pack(fill="x", padx=12, pady=(0,8))
        tk.Label(mk, text="Marqueurs : clic gauche = A, clic droit = B").pack(side="left", padx=6)
        self.lbl_ab = tk.Label(mk, text="A–B : --.-- s", font=("Consolas", 10, "bold"))
        self.lbl_ab.pack(side="left", padx=10)
        self.lbl_stats = tk.Label(mk, text=self.stats_label_var.get(), font=("Consolas", 10))
        self.lbl_stats.pack(side="right", padx=6)

        # mpl events
        self.canvas.mpl_connect("button_press_event", self._on_mpl_click)
        self.canvas.mpl_connect("motion_notify_event", self._on_mpl_move)
        self.canvas.mpl_connect("axes_leave_event", self._on_mpl_leave)

    # ---------------------------------
    # Serial connect / disconnect
    # ---------------------------------

    def connect(self):
        if self.ser and self.ser.is_open:
            messagebox.showinfo("Info", "Déjà connecté.")
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("Attention", "Aucun port sélectionné.")
            return
        try:
            baud = int(self.baud_var.get())
        except Exception:
            baud = DEFAULT_BAUD
        try:
            self.ser = open_serial(port, baud)
            idn = try_detect_idn(self.ser)
            self.idn_var.set(idn if idn else f"Connecté à {port} @ {baud} bps")
            if not (idn and looks_like_xdm(idn)):
                messagebox.showinfo("Info", f"Connecté, mais IDN inattendu : {idn}")
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible d'ouvrir {port}\n{e}")
            self.ser = None

    def auto_connect(self):
        if self.ser and self.ser.is_open:
            messagebox.showinfo("Info", "Déjà connecté.")
            return
        candidates = list_serial_ports()
        if not candidates:
            messagebox.showwarning("Attention", "Aucun port série détecté.")
            return
        for p in candidates:
            try:
                s = open_serial(p, int(self.baud_var.get()))
                idn = try_detect_idn(s)
                if looks_like_xdm(idn):
                    self.ser = s
                    self.port_var.set(p)
                    self.idn_var.set(idn)
                    messagebox.showinfo("OK", f"Multimètre détecté sur {p}\n{idn}")
                    return
                s.close()
            except Exception:
                continue
        messagebox.showwarning("Attention", "Aucun OWON/XDM détecté via *IDN?*.")

    def disconnect(self):
        self.stop_read_loop()
        if self.ser and self.ser.is_open:
            try: self.ser.close()
            except Exception: pass
        self.ser = None
        self.idn_var.set("Non connecté")

    # ---------------------------------
    # Mode / Rate control
    # ---------------------------------

    def _set_mode_scpi(self, label):
        # mapping label -> SCPI command
        mapping = {
            "VOLT DC":"CONF:VOLT:DC",
            "VOLT AC":"CONF:VOLT:AC",
            "CURR DC":"CONF:CURR:DC",
            "CURR AC":"CONF:CURR:AC",
            "RES":"CONF:RES",
            "CONT":"CONF:CONT",
            "CAP":"CONF:CAP",
            "DIOD":"CONF:DIOD",
            "FREQ":"CONF:FREQ",
            "TEMP":"CONF:TEMP"
        }
        cmd = mapping.get(label)
        if not cmd:
            return
        scpi_write(self.ser, cmd)
        # force a short pause to let DMM settle
        time.sleep(0.2)
        # clear & reset plot data
        self.clear_data()
        # highlight
        self._highlight_mode_btn(label)
        # store
        self.mode_var.set(label)
        self.lbl_mode.config(text=f"Mode : {label}")

    def _set_rate_scpi(self, rate_code):
        # rate_code in {"S","M","F"}
        scpi_write(self.ser, f"RATE {rate_code}")
        self._highlight_rate_btn(rate_code)

    def _highlight_mode_btn(self, label):
        # reset previous
        if self.active_mode_btn and self.active_mode_btn in self.mode_buttons.values():
            # reset styles
            self.active_mode_btn.config(bg="SystemButtonFace", fg="black")
        # highlight new
        b = self.mode_buttons.get(label)
        if b:
            b.config(bg="#444", fg="white")
            self.active_mode_btn = b

    def _highlight_rate_btn(self, rate_code):
        # reset previous
        if self.active_rate_btn and self.active_rate_btn in self.rate_buttons.values():
            self.active_rate_btn.config(bg="SystemButtonFace", fg="black")
        b = self.rate_buttons.get(rate_code)
        if b:
            b.config(bg="#444", fg="white")
            self.active_rate_btn = b

    # ---------------------------------
    # Acquisition loop
    # ---------------------------------

    def toggle_run(self):
        if self.read_thread and self.read_thread.is_alive():
            self.stop_read_loop()
            self.btn_start.config(text="▶️ Start")
        else:
            if not (self.ser and self.ser.is_open):
                messagebox.showwarning("Attention", "Connecte d'abord le port série.")
                return
            self.start_read_loop()
            self.btn_start.config(text="⏸ Pause")

    def start_read_loop(self):
        self.read_stop.clear()
        # reset acquisition timing but don't nuke markers yet
        self.t0 = time.time()
        self.read_thread = threading.Thread(target=self._read_worker, daemon=True)
        self.read_thread.start()

    def stop_read_loop(self):
        self.read_stop.set()
        if self.read_thread:
            self.read_thread.join(timeout=0.5)
        self.read_thread = None

    def _read_worker(self):
        while not self.read_stop.is_set():
            start_t = time.time()

            mode = query_mode(self.ser)
            raw = query_value(self.ser)
            val = parse_float(raw)
            unit = mode_to_unit(mode)
            self.data_q.put(("sample", time.time(), mode, val, unit))

            if mode and mode != self.last_mode:
                self.data_q.put(("mode_change", mode))

            # update range every ~3s
            if int(start_t) % 3 == 0:
                rng = query_range(self.ser)
                self.data_q.put(("range", rng))

            # poll rate
            hz = self._get_poll_hz()
            if hz < MIN_POLL_HZ:
                hz = MIN_POLL_HZ
            period = 1.0 / hz
            remain = period - (time.time() - start_t)
            if remain > 0:
                time.sleep(remain)
        # ---------------------------------
    # Apply duration field
    # ---------------------------------
    def apply_duration_from_entry(self):
        """Applique la durée saisie au champ texte."""
        try:
            dur = float(self.duration_entry_var.get().strip().replace(",", "."))
            if dur <= 0:
                raise ValueError
            self.duration.set(dur)
        except Exception:
            messagebox.showwarning("Valeur invalide", "Durée incorrecte. Ex: 30")

    def _get_poll_hz(self) -> float:
        try:
            hz = float(self.poll_hz_var.get().strip())
            return hz
        except Exception:
            return 2.0

    # ---------------------------------
    # Periodic UI update
    # ---------------------------------

    def _schedule_ui_update(self):
        self._drain_queue_and_update_ui()
        self.after(50, self._schedule_ui_update)

    def _drain_queue_and_update_ui(self):
        updated = False
        unit_for_axis = None
        last_val_for_windows = None

        while True:
            try:
                item = self.data_q.get_nowait()
            except queue.Empty:
                break

            if not item:
                continue

            if item[0] == "sample":
                _, t_abs, mode, val, unit = item
                if not self.data:
                    self.t0 = t_abs
                t_rel = t_abs - self.t0

                # clamp negatives if mode should not go negative
                if val is not None and not allow_negative(mode) and val < 0:
                    val = 0.0

                # record raw sample
                self.data.append((t_rel, val))

                # UI mode/value
                self.mode_var.set(mode if mode else "—")
                self.lbl_mode.config(text=f"Mode : {self.mode_var.get()}")

                self.value_var.set(humanize_value(val, unit))
                unit_for_axis = unit
                self.last_mode = mode
                last_val_for_windows = val
                updated = True

            elif item[0] == "range":
                rng = item[1]
                if rng:
                    self.range_var.set(rng)
                else:
                    self.range_var.set("Auto / —")
                self.lbl_range.config(text=f"Gamme : {self.range_var.get()}")

            elif item[0] == "mode_change":
                # full clear on mode change
                self.clear_data()

        # feed Go/No-Go window
        if self.gonogo_win and tk.Toplevel.winfo_exists(self.gonogo_win) and last_val_for_windows is not None:
            try:
                self.gonogo_win.update_with_measure(last_val_for_windows)
            except Exception:
                pass

        if updated:
            # update Y label with the latest unit
            unit_axis = unit_for_axis or mode_to_unit(self.mode_var.get())
            ylabel = f"Valeur ({unit_axis})" if unit_axis else "Valeur"
            self.ax.set_ylabel(ylabel)
            self._redraw_plot()

    # ---------------------------------
    # Plot / markers / stats
    # ---------------------------------

    def clear_data(self):
        self.data.clear()
        self.markerA = self.markerB = None
        self.stats_label_var.set("A–B stats : n=0  min=—  max=—  mean=—")
        if self.lbl_stats:
            self.lbl_stats.config(text=self.stats_label_var.get())
        self.t0 = time.time()
        self._redraw_plot()

    def _redraw_plot(self):
        # parse duration
        try:
            dur_s = float(self.duration.get())
        except Exception:
            try:
                dur_s = float(self.duration_entry_var.get())
            except Exception:
                dur_s = 30.0

        # parse y max limit
        plot_max = None
        if self.max_plot_var.get().strip():
            try:
                plot_max = float(self.max_plot_var.get().strip().replace(",", "."))
                if plot_max <= 0:
                    plot_max = None
            except Exception:
                plot_max = None

        if not self.data:
            self.ax.cla()
            self.ax.set_title("Mesure en temps réel")
            self.ax.set_xlabel("Temps (s)")
            unit = mode_to_unit(self.mode_var.get())
            ylabel = f"Valeur ({unit})" if unit else "Valeur"
            self.ax.set_ylabel(ylabel)
            self.canvas.draw_idle()
            return

        tmax = self.data[-1][0]
        tmin = max(0.0, tmax - dur_s)

        vis_t = []
        vis_v = []

        allow_neg = allow_negative(self.mode_var.get())

        for (t_rel, v) in self.data:
            if t_rel < tmin:
                continue
            if v is None:
                continue
            # apply y max filter
            if plot_max is not None and v > plot_max:
                continue
            # clamp negatives if not allowed
            if not allow_neg and v < 0:
                v = 0.0
            vis_t.append(t_rel)
            vis_v.append(v)

        self.ax.clear()
        self.ax.set_title("Mesure en temps réel")
        self.ax.set_xlabel("Temps (s)")
        unit = mode_to_unit(self.mode_var.get())
        ylabel = f"Valeur ({unit})" if unit else "Valeur"
        self.ax.set_ylabel(ylabel)

        if vis_t:
            self.ax.plot(vis_t, vis_v, lw=1.5)
            self.ax.set_xlim(tmin, tmax)

            # Y range
            if plot_max is not None:
                ymin = 0.0 if not allow_neg else min(vis_v)
                ymax = plot_max
            else:
                vmin = min(vis_v)
                vmax = max(vis_v)
                if not allow_neg:
                    vmin = max(0.0, vmin)
                if vmin == vmax:
                    vmin -= 1
                    vmax += 1
                margin = (vmax - vmin) * 0.05
                ymin = vmin - margin
                ymax = vmax + margin
                if not allow_neg:
                    ymin = max(0.0, ymin)
            self.ax.set_ylim(ymin, ymax)

        # markers & stats
        if self.markerA is not None:
            self.ax.axvline(self.markerA, linestyle="--", color="red", alpha=0.8)
        if self.markerB is not None:
            self.ax.axvline(self.markerB, linestyle="--", color="blue", alpha=0.8)

        self._update_ab_and_stats(vis_t, vis_v)
        self.canvas.draw_idle()

    def _on_mpl_click(self, event):
        if event.inaxes != self.ax or event.xdata is None:
            return
        if event.button == 1:
            self.markerA = float(event.xdata)
        elif event.button == 3:
            self.markerB = float(event.xdata)
        self._redraw_plot()

    def _on_mpl_move(self, event):
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            self._mouse_info.set_visible(False)
            self.canvas.draw_idle()
            return
        dur = self.duration.get()
        self._mouse_info.xy = (event.xdata, event.ydata)
        self._mouse_info.set_text(f"Fenêtre: {dur:.1f} s\n t={event.xdata:.3f} s")
        self._mouse_info.set_visible(True)
        self.canvas.draw_idle()

    def _on_mpl_leave(self, event):
        self._mouse_info.set_visible(False)
        self.canvas.draw_idle()

    def _update_ab_and_stats(self, tt, vv):
        if self.markerA is None or self.markerB is None or not tt:
            self.lbl_ab.config(text="A–B : --.-- s")
            self.stats_label_var.set("A–B stats : n=0  min=—  max=—  mean=—")
            self.lbl_stats.config(text=self.stats_label_var.get())
            return

        ta = min(self.markerA, self.markerB)
        tb = max(self.markerA, self.markerB)
        dt = tb - ta
        self.lbl_ab.config(text=f"A–B : {dt:.3f} s")

        samples = [v for (t, v) in zip(tt, vv) if (t >= ta and t <= tb)]
        if not samples:
            self.stats_label_var.set("A–B stats : n=0  min=—  max=—  mean=—")
            self.lbl_stats.config(text=self.stats_label_var.get())
            return

        n = len(samples)
        vmin = min(samples)
        vmax = max(samples)
        mean = sum(samples) / n
        unit = mode_to_unit(self.mode_var.get())
        self.stats_label_var.set(
            f"A–B stats : n={n}  min={humanize_value(vmin, unit)}  max={humanize_value(vmax, unit)}  mean={humanize_value(mean, unit)}"
        )
        self.lbl_stats.config(text=self.stats_label_var.get())

    # ---------------------------------
    # Tools: Go/No-Go, Mini, CSV, PNG
    # ---------------------------------

    def open_gonogo(self):
        if self.gonogo_win and tk.Toplevel.winfo_exists(self.gonogo_win):
            self.gonogo_win.lift()
            return
        self.gonogo_win = GoNoGoWindow(self, self._get_current_unit)

    def open_mini(self):
        if self.mini_win and tk.Toplevel.winfo_exists(self.mini_win):
            self.mini_win.lift()
            return
        self.mini_win = MiniDisplayWindow(
            self,
            self._get_current_mode,
            self._get_current_range,
            self._get_current_value_string
        )

    def _get_current_unit(self):
        return mode_to_unit(self.mode_var.get())

    def _get_current_mode(self):
        return self.mode_var.get()

    def _get_current_range(self):
        return self.range_var.get()

    def _get_current_value_string(self):
        return self.value_var.get()

    def save_csv(self):
        if not self.data:
            messagebox.showwarning("Attention", "Pas de données à sauvegarder.")
            return
        fn = filedialog.asksaveasfilename(
            title="Sauvegarder CSV",
            defaultextension=".csv",
            initialfile=f"xdm_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")]
        )
        if not fn:
            return
        mode = self.mode_var.get()
        unit = mode_to_unit(mode)
        t_abs0 = self.t0
        try:
            with open(fn, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["timestamp_iso", "t_rel_s", "mode", "value_raw", f"value_human({unit})"])
                for (t_rel, v) in self.data:
                    ts = datetime.fromtimestamp(t_abs0 + t_rel).isoformat(timespec="milliseconds")
                    human = humanize_value(v, unit)
                    w.writerow([ts, f"{t_rel:.6f}", mode, "" if v is None else f"{v:.12g}", human])
            messagebox.showinfo("OK", f"CSV sauvegardé : {fn}")
        except Exception as e:
            messagebox.showerror("Erreur", f"Échec sauvegarde CSV\n{e}")

    def save_png(self):
        fn = filedialog.asksaveasfilename(
            title="Sauvegarder PNG",
            defaultextension=".png",
            initialfile=f"xdm_plot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
            filetypes=[("PNG", "*.png"), ("All files", "*.*")]
        )
        if not fn:
            return
        try:
            self.fig.savefig(fn, dpi=150, bbox_inches="tight")
            messagebox.showinfo("OK", f"PNG sauvegardé : {fn}")
        except Exception as e:
            messagebox.showerror("Erreur", f"Échec sauvegarde PNG\n{e}")

# ---------------------------------
# Main
# ---------------------------------

if __name__ == "__main__":
    app = XDMScope()
    app.mainloop()
