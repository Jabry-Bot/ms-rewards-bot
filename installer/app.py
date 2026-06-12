"""
GUI del instalador autocontenido (setup.exe) de ms_rewards.

Toda la lógica pura (rutas, detección, construcción de comandos) vive en
installer/bootstrap.py. Aquí solo: pintamos la UI, lanzamos subprocesos y
volcamos su salida en vivo a un panel de log. Nada bloquea el hilo de Tk: la
secuencia de instalación corre en un hilo daemon y se comunica con la UI a
través de una cola que se drena con self.after().
"""
from __future__ import annotations

import os
import sys
import queue
import subprocess
import threading
import tempfile
import urllib.request

from pathlib import Path

import customtkinter as ctk
from tkinter import messagebox, filedialog

# Importamos el bootstrap con fallback: congelado puede no existir el paquete.
try:
    from installer import bootstrap as boot
except ImportError:
    import bootstrap as boot


# Sin ventanas de consola para los subprocesos que capturamos.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

# Colores para estados del checklist.
COLOR_OK = "#3ad17a"
COLOR_BAD = "#e05260"
COLOR_WARN = "#e0b052"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("ms_rewards — Instalador")
        self.geometry("760x640")
        self.minsize(680, 560)

        # Cola de líneas de log (alimentada desde hilos, drenada en la UI).
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        # Proceso en curso (para poder matarlo al cerrar la ventana).
        self.proc: subprocess.Popen | None = None
        # Filas del checklist: lista de (icono_label, texto_label).
        self._tool_rows: list[tuple[ctk.CTkLabel, ctk.CTkLabel]] = []
        # Flag para no lanzar dos secuencias a la vez.
        self._busy = False

        # --- Carpeta de instalación ----------------------------------------
        # In-repo: el dev/maintainer ejecuta dentro del repo (ms_rewards/ al
        # lado). Trabajamos en el sitio, sin clonar. Si NO, modo clone: usamos
        # una carpeta de instalación estable por-usuario y clonamos el repo ahí.
        self._in_repo = boot.is_in_repo(boot.ROOT)
        if self._in_repo:
            self.install_root: Path = boot.ROOT
        else:
            self.install_root = boot.default_install_dir()
        self.paths = boot.InstallPaths(self.install_root)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Escaneo inicial y arranque del drenado de la cola.
        self.refresh_tools()
        self.after(100, self._drain_log)

    # --- Construcción de la UI -------------------------------------------
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)  # el log se estira

        # Cabecera.
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 6))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Instalador de ms_rewards",
            font=ctk.CTkFont(size=26, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Instalará Python, Git, Microsoft Edge y las dependencias del bot.",
            font=ctk.CTkFont(size=13),
            text_color="#9aa4b2",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        # Fila de carpeta de instalación (solo relevante en modo clone).
        folder_box = ctk.CTkFrame(self)
        folder_box.grid(row=1, column=0, sticky="ew", padx=20, pady=(2, 6))
        folder_box.grid_columnconfigure(0, weight=1)

        self.folder_label = ctk.CTkLabel(
            folder_box, text="", anchor="w", justify="left",
            font=ctk.CTkFont(size=12),
        )
        self.folder_label.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=8)

        self.btn_change_dir = ctk.CTkButton(
            folder_box, text="📁 Cambiar…", width=120,
            command=self._choose_install_dir,
        )
        self.btn_change_dir.grid(row=0, column=1, sticky="e", padx=(0, 10), pady=8)
        if self._in_repo:
            # Dentro del repo no se elige carpeta: se trabaja en el sitio.
            self.btn_change_dir.configure(state="disabled")
        self._refresh_folder_label()

        # Checklist de herramientas.
        tools_box = ctk.CTkFrame(self)
        tools_box.grid(row=2, column=0, sticky="ew", padx=20, pady=8)
        tools_box.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(tools_box, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            top, text="Estado de las herramientas",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            top, text="🔄 Re-comprobar", width=130, command=self.refresh_tools,
        ).grid(row=0, column=1, sticky="e")

        # Frame scrollable que contiene una fila por herramienta.
        self.tools_frame = ctk.CTkScrollableFrame(tools_box, height=140)
        self.tools_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.tools_frame.grid_columnconfigure(1, weight=1)

        # Construimos filas vacías reutilizables (una por herramienta del plan).
        for i, status in enumerate(boot.scan_tools()):
            icon = ctk.CTkLabel(self.tools_frame, text="…", width=24,
                                font=ctk.CTkFont(size=16))
            icon.grid(row=i, column=0, padx=(4, 6), pady=3, sticky="w")
            text = ctk.CTkLabel(self.tools_frame, text=status.tool.label,
                                anchor="w", justify="left")
            text.grid(row=i, column=1, padx=2, pady=3, sticky="ew")
            self._tool_rows.append((icon, text))

        # Botonera de acciones.
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=20, pady=(2, 6))
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)

        self.btn_install = ctk.CTkButton(
            actions, text="⚙ Instalar todo", height=46,
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._start_install,
        )
        self.btn_install.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_configure = ctk.CTkButton(
            actions, text="👤 Configurar cuenta y registrar tarea", height=46,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2b6f4e", hover_color="#235b40",
            command=self._start_configure,
        )
        self.btn_configure.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # Barra de estado.
        self.status_label = ctk.CTkLabel(
            self, text="Listo.", anchor="w", text_color="#9aa4b2",
        )
        self.status_label.grid(row=4, column=0, sticky="ew", padx=22, pady=(0, 2))

        # Panel de log (solo lectura, monoespaciado).
        log_box = ctk.CTkFrame(self)
        log_box.grid(row=5, column=0, sticky="nsew", padx=20, pady=(2, 18))
        log_box.grid_columnconfigure(0, weight=1)
        log_box.grid_rowconfigure(0, weight=1)

        self.log = ctk.CTkTextbox(
            log_box, font=ctk.CTkFont(family="Consolas", size=12), wrap="word",
        )
        self.log.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.log.configure(state="disabled")

    # --- Logging ----------------------------------------------------------
    def _log(self, line: str) -> None:
        """Empuja una línea a la cola. Seguro desde cualquier hilo."""
        self.log_queue.put(line)

    def _drain_log(self) -> None:
        """Vuelca la cola al textbox. SIEMPRE corre en el hilo de Tk."""
        wrote = False
        try:
            while True:
                line = self.log_queue.get_nowait()
                if not wrote:
                    self.log.configure(state="normal")
                    wrote = True
                self.log.insert("end", line + "\n")
        except queue.Empty:
            pass
        if wrote:
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(100, self._drain_log)

    def _set_status(self, text: str) -> None:
        # Programado en el hilo de UI para no tocar Tk desde hilos de trabajo.
        self.after(0, lambda: self.status_label.configure(text=text))

    # --- Carpeta de instalación ------------------------------------------
    def _set_install_root(self, root: Path) -> None:
        """Fija la carpeta de instalación y RECALCULA siempre self.paths."""
        self.install_root = root
        self.paths = boot.InstallPaths(self.install_root)

    def _refresh_folder_label(self) -> None:
        """Repinta la etiqueta de la carpeta de instalación."""
        if self._in_repo:
            text = (f"Carpeta de instalación: {self.install_root}  "
                    "(ejecutando dentro del repo)")
        else:
            text = f"Carpeta de instalación: {self.install_root}"
        self.folder_label.configure(text=text)

    def _choose_install_dir(self) -> None:
        """Abre un diálogo para elegir la carpeta de instalación (modo clone)."""
        if self._in_repo or self._busy:
            return
        chosen = filedialog.askdirectory(initialdir=str(self.install_root.parent))
        if not chosen:
            return
        chosen_path = Path(chosen)
        # Anexar el nombre del proyecto si la carpeta elegida no lo es ya.
        if chosen_path.name != "ms-rewards-bot":
            chosen_path = chosen_path / "ms-rewards-bot"
        self._set_install_root(chosen_path)
        self._refresh_folder_label()
        self.refresh_tools()

    # --- Checklist --------------------------------------------------------
    def refresh_tools(self) -> None:
        """Re-escanea las herramientas y repinta las filas."""
        try:
            statuses = boot.scan_tools()
        except Exception as exc:  # noqa: BLE001 — la UI nunca debe morir
            self._log(f"[ERROR] No se pudo escanear herramientas: {exc}")
            return

        for (icon, text), st in zip(self._tool_rows, statuses):
            if st.installed:
                icon.configure(text="✅", text_color=COLOR_OK)
                detail = str(st.path)
            else:
                color = COLOR_BAD if st.tool.required else COLOR_WARN
                icon.configure(text="❌", text_color=color)
                detail = "no encontrado"
            text.configure(text=f"{st.tool.label}  —  {detail}")

        # El paso final solo se habilita si el venv ya está listo.
        try:
            ready = self.paths.venv_ready
        except Exception:  # noqa: BLE001
            ready = False
        state = "normal" if (ready and not self._busy) else "disabled"
        self.btn_configure.configure(state=state)

    # --- Habilitar/deshabilitar botonera ---------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        # Programar en el hilo de UI.
        def apply() -> None:
            self.btn_install.configure(state=state)
            # El botón de cambiar carpeta solo está activo en modo clone y ocioso.
            if not self._in_repo:
                self.btn_change_dir.configure(state=state)
            # configure depende también del venv; lo resuelve refresh_tools.
            if busy:
                self.btn_configure.configure(state="disabled")
            else:
                self.refresh_tools()
        self.after(0, apply)

    # --- Ejecución de subprocesos con captura ----------------------------
    def run_and_capture(self, cmd: list[str], cwd: str | None = None) -> int:
        """
        Corre `cmd` SÍNCRONAMENTE (pensado para llamarse dentro del hilo de
        trabajo) y empuja cada línea de su salida a la cola de log. Devuelve el
        returncode. Captura excepciones y las trata como fallo (returncode != 0).
        """
        printable = " ".join(cmd)
        self._log(f"$ {printable}")
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                bufsize=1,
                cwd=cwd,
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"[ERROR] No se pudo lanzar el proceso: {exc}")
            self.proc = None
            return 1

        try:
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self._log(line.rstrip("\n"))
            self.proc.wait()
            rc = self.proc.returncode if self.proc.returncode is not None else 1
        except Exception as exc:  # noqa: BLE001
            self._log(f"[ERROR] Lectura de salida interrumpida: {exc}")
            rc = 1
        finally:
            self.proc = None
        return rc

    def _download(self, url: str, dest: str) -> bool:
        """Descarga url a dest. Loguea inicio/fin. True si OK."""
        self._log(f"⬇ Descargando {url}")
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[ERROR] Descarga fallida: {exc}")
            return False
        self._log(f"   guardado en {dest}")
        return True

    # --- Secuencia de instalación ----------------------------------------
    def _start_install(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._set_status("Instalando…")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self) -> None:
        """Secuencia completa de instalación. Corre en hilo daemon."""
        try:
            ok = self._run_install_steps()
        except Exception as exc:  # noqa: BLE001 — nada tumba la UI
            self._log(f"[ERROR] Fallo inesperado: {exc}")
            self._fail("Error inesperado durante la instalación.", str(exc))
            ok = False
        finally:
            self.after(0, self.refresh_tools)
            self._set_busy(False)

        if ok:
            self._set_status("Instalación completada.")
            self._log("\n✅ Instalación completada. Ya puedes configurar la cuenta.")
            self.after(0, lambda: messagebox.showinfo(
                "ms_rewards",
                "Instalación completada.\n\nAhora pulsa "
                "«Configurar cuenta y registrar tarea».",
            ))

    def _fail(self, summary: str, detail: str) -> None:
        """Loguea en rojo (conceptual) y muestra messagebox de error."""
        self._log(f"[ERROR] {summary}")
        self._set_status(f"Error: {summary}")
        self.after(0, lambda: messagebox.showerror("ms_rewards — Error",
                                                    f"{summary}\n\n{detail}"))

    def _run_install_steps(self) -> bool:
        """Ejecuta los 8 pasos en orden. Devuelve False si un paso REQUIRED falla."""
        # --- [1/8] Python ------------------------------------------------
        self._log("\n[1/8] Python")
        sys_py = boot.detect_python_path()
        need_python = True
        if sys_py is not None:
            ver = self._python_version(sys_py)
            if boot.is_supported_python(ver):
                self._log(f"   OK — Python {ver} en {sys_py}")
                need_python = False
            else:
                self._log(f"   Versión insuficiente ({ver}); se reinstalará.")

        if need_python:
            if not self._install_python():
                self._fail("No se pudo instalar Python.",
                           "Instala Python 3.10+ manualmente y reintenta.")
                return False
            sys_py = boot.detect_python_path()
            if sys_py is None:
                self._fail("Python instalado pero no detectado.",
                           "Reinicia la sesión para refrescar el PATH.")
                return False
            self._log(f"   OK — Python detectado en {sys_py}")

        # --- [2/8] Git ---------------------------------------------------
        self._log("\n[2/8] Git")
        if boot.detect_git() is not None:
            self._log(f"   OK — Git en {boot.detect_git()}")
        else:
            if not self._install_git():
                self._fail("No se pudo instalar Git.",
                           "Instala Git for Windows manualmente y reintenta.")
                return False
            if boot.detect_git() is None:
                self._fail("Git instalado pero no detectado.",
                           "Reinicia la sesión para refrescar el PATH.")
                return False
            self._log(f"   OK — Git detectado en {boot.detect_git()}")

        # --- [3/8] Edge (no required: no aborta) -------------------------
        self._log("\n[3/8] Microsoft Edge")
        if boot.detect_edge() is not None:
            self._log(f"   OK — Edge en {boot.detect_edge()}")
        else:
            if boot.winget_available():
                self._log("   Edge no detectado; instalando con winget…")
                rc = self.run_and_capture(boot.winget_install_cmd("edge"))
                if rc == 0 and boot.detect_edge() is not None:
                    self._log(f"   OK — Edge en {boot.detect_edge()}")
                else:
                    self._log("   [WARN] No se pudo instalar Edge automáticamente; "
                              "instálalo manualmente si hace falta.")
            else:
                self._log("   [WARN] Edge no detectado; instálalo manualmente. "
                          "(Suele venir preinstalado en Windows 10/11.)")

        # --- [4/8] Código del bot (clonar el repo si falta) --------------
        self._log("\n[4/8] Código del bot")
        if self.paths.has_source:
            self._log(f"   OK — código ya presente en {self.paths.rewards_dir}; "
                      "omito clonado.")
            # Si es un clon git previo, intentamos actualizarlo (sin abortar).
            if not self._in_repo and (self.paths.base / ".git").exists():
                git = boot.detect_git()
                if git is not None:
                    self._log("   Actualizando clon existente (git pull)…")
                    rc = self.run_and_capture(
                        boot.git_pull_cmd(git, self.paths.base))
                    if rc != 0:
                        self._log(f"   [WARN] git pull devolvió código {rc}; "
                                  "continúo con el código actual.")
        else:
            git = boot.detect_git()
            if git is None:
                self._fail("No se puede clonar el bot sin Git.",
                           "Git no está disponible; instálalo y reintenta.")
                return False
            # git clone exige un destino inexistente o vacío. Si ya existe con
            # contenido (pero sin el código del bot), avisamos claramente y
            # apuntamos al botón "Cambiar…" en vez de fallar con un error críptico.
            try:
                if self.install_root.exists() and any(self.install_root.iterdir()):
                    self._fail(
                        "La carpeta de instalación no está vacía.",
                        f"{self.install_root} ya tiene contenido que no es el bot. "
                        "Usa «📁 Cambiar…» para elegir una carpeta vacía o nueva.")
                    return False
            except OSError:
                pass
            try:
                self.install_root.parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                self._fail("No se pudo preparar la carpeta de instalación.",
                           str(exc))
                return False
            self._log(f"   Clonando el repo en {self.install_root}…")
            rc = self.run_and_capture(
                boot.git_clone_cmd(git, self.install_root))
            if rc != 0:
                self._fail("No se pudo clonar el repositorio del bot.",
                           f"git clone devolvió código {rc}. La carpeta destino "
                           "debe estar vacía o no existir.")
                return False
            if not self.paths.has_source:
                self._fail("Clonado completado pero no se encontró el código.",
                           f"No existe {self.paths.rewards_dir} tras clonar.")
                return False
            self._log(f"   OK — repo clonado en {self.install_root}.")

        # --- [5/8] venv --------------------------------------------------
        self._log("\n[5/8] Entorno virtual (.venv)")
        if self.paths.venv_ready:
            self._log("   OK — el venv ya existe; se omite.")
        else:
            if sys_py is None:
                self._fail("No hay Python del sistema para crear el venv.", "")
                return False
            rc = self.run_and_capture(
                boot.venv_create_cmd(sys_py, self.paths.venv_dir))
            if rc != 0 or not self.paths.venv_ready:
                self._fail("No se pudo crear el entorno virtual.",
                           f"venv devolvió código {rc}.")
                return False
            self._log("   OK — venv creado.")

        # --- [6/8] pip ---------------------------------------------------
        self._log("\n[6/8] Dependencias (pip)")
        rc = self.run_and_capture(boot.pip_upgrade_cmd(self.paths.venv_py))
        if rc != 0:
            self._fail("Fallo al actualizar pip.", f"pip devolvió código {rc}.")
            return False
        rc = self.run_and_capture(
            boot.pip_requirements_cmd(self.paths.venv_py, self.paths.requirements))
        if rc != 0:
            self._fail("Fallo al instalar las dependencias del bot.",
                       f"pip install -r devolvió código {rc}.")
            return False
        self._log("   OK — dependencias instaladas.")

        # --- [7/8] drivers patchright (no aborta) ------------------------
        # El navegador se elige más tarde en setup_cli.py, así que instalamos
        # los drivers de AMBOS (chrome y edge) para cubrir cualquier elección.
        self._log("\n[7/8] Drivers de patchright (chrome + edge)")
        for browser in ("chrome", "edge"):
            rc = self.run_and_capture(
                boot.patchright_drivers_cmd(self.paths.venv_py, browser=browser))
            if rc == 0:
                self._log(f"   OK — drivers de {browser} instalados.")
            else:
                self._log(f"   [WARN] No se pudieron instalar los drivers de "
                          f"{browser} (código {rc}); puedes reintentar más tarde.")

        # --- [8/8] Panel de control y acceso directo (best-effort) -------
        # Descarga el panel (.exe) si falta y crea un acceso directo en el
        # Escritorio. Ningún fallo aquí debe abortar la instalación.
        self._log("\n[8/8] Panel de control y acceso directo")
        panel_exe = boot.panel_exe_path(self.paths.base)
        if panel_exe.exists():
            self._log(f"   panel ya presente, omito descarga ({panel_exe}).")
        else:
            self._log(f"   Descargando el panel desde {boot.PANEL_EXE_URL}…")
            try:
                urllib.request.urlretrieve(boot.PANEL_EXE_URL, str(panel_exe))
                self._log(f"   OK — panel guardado en {panel_exe}.")
            except Exception as exc:  # noqa: BLE001 — best-effort, no aborta
                self._log(f"   [WARN] No se pudo descargar el panel ({exc}); "
                          "puedes bajarlo del release manualmente.")

        if panel_exe.exists():
            shortcut = boot.desktop_shortcut_path()
            rc = self.run_and_capture(
                boot.create_shortcut_cmd(panel_exe, shortcut, self.paths.base))
            if rc == 0:
                self._log(f"   Acceso directo creado en el Escritorio: {shortcut}")
            else:
                self._log(f"   [WARN] No se pudo crear el acceso directo "
                          f"(código {rc}); puedes crearlo manualmente.")

        return True

    def _python_version(self, python_path) -> tuple | None:
        """Ejecuta `<python> --version` y parsea la versión. None si falla."""
        try:
            out = subprocess.run(
                [str(python_path), "--version"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", creationflags=CREATE_NO_WINDOW, timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"   [WARN] No se pudo consultar la versión: {exc}")
            return None
        # `python --version` puede escribir en stdout o stderr según versión.
        return boot.parse_python_version((out.stdout or "") + (out.stderr or ""))

    def _install_python(self) -> bool:
        """Instala Python: winget primero, descarga directa como fallback."""
        if boot.winget_available():
            self._log("   Instalando Python con winget…")
            rc = self.run_and_capture(boot.winget_install_cmd("python"))
            if rc == 0 and boot.detect_python_path() is not None:
                return True
            self._log("   [WARN] winget falló; probando instalador oficial…")

        dest = os.path.join(tempfile.gettempdir(),
                            f"python-{boot.PYTHON_VERSION}-amd64.exe")
        if not self._download(boot.PYTHON_INSTALLER_URL, dest):
            return False
        self._log("   Ejecutando instalador de Python (silencioso)…")
        rc = self.run_and_capture(boot.python_direct_install_cmd(dest))
        return rc == 0

    def _install_git(self) -> bool:
        """Instala Git: winget primero, descarga directa como fallback."""
        if boot.winget_available():
            self._log("   Instalando Git con winget…")
            rc = self.run_and_capture(boot.winget_install_cmd("git"))
            if rc == 0 and boot.detect_git() is not None:
                return True
            self._log("   [WARN] winget falló; probando instalador oficial…")

        dest = os.path.join(tempfile.gettempdir(),
                            f"Git-{boot.GIT_VERSION}-64-bit.exe")
        if not self._download(boot.GIT_INSTALLER_URL, dest):
            return False
        self._log("   Ejecutando instalador de Git (silencioso)…")
        rc = self.run_and_capture(boot.git_direct_install_cmd(dest))
        return rc == 0

    # --- Paso final: configurar cuenta + registrar tarea -----------------
    def _start_configure(self) -> None:
        if self._busy:
            return
        if not self.paths.venv_ready:
            messagebox.showwarning(
                "ms_rewards",
                "El entorno virtual aún no está listo. Pulsa «Instalar todo» primero.",
            )
            return
        self._set_busy(True)
        self._set_status("Configurando cuenta…")
        threading.Thread(target=self._configure_worker, daemon=True).start()

    def _configure_worker(self) -> None:
        try:
            ok = self._run_configure()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[ERROR] Fallo inesperado en la configuración: {exc}")
            self._fail("Error inesperado durante la configuración.", str(exc))
            ok = False
        finally:
            self._set_busy(False)
            self.after(0, self.refresh_tools)

        if ok:
            self._set_status("Configuración completada.")
            self._log("\n✅ ¡Todo listo! Cuenta configurada y tarea registrada.")
            self.after(0, lambda: messagebox.showinfo(
                "ms_rewards",
                "¡Instalación finalizada!\n\nLa cuenta está configurada y la "
                "tarea programada quedó registrada. El bot correrá automáticamente.",
            ))

    def _run_configure(self) -> bool:
        # 1) Configuración interactiva en CONSOLA NUEVA (input/getpass/2FA).
        self._log("\nAbriendo ventana de configuración…")
        self._log("   (rellena los datos en la consola nueva; espera a que termine)")
        try:
            proc = subprocess.Popen(
                [str(self.paths.venv_py), str(self.paths.setup_cli)],
                cwd=str(self.paths.rewards_dir),
                creationflags=CREATE_NEW_CONSOLE,
            )
            self.proc = proc
            proc.wait()
            rc = proc.returncode
        except Exception as exc:  # noqa: BLE001
            self._fail("No se pudo abrir la configuración interactiva.", str(exc))
            return False
        finally:
            self.proc = None

        if rc != 0:
            self._fail("La configuración de la cuenta no finalizó correctamente.",
                       f"setup_cli devolvió código {rc}.")
            return False
        self._log("   OK — configuración de cuenta completada.")

        # 2) Registrar la Scheduled Task vía PowerShell (salida capturada).
        self._log("\nRegistrando la tarea programada…")
        rc = self.run_and_capture([
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(self.paths.install_task_ps1),
        ])
        if rc != 0:
            self._fail("No se pudo registrar la tarea programada.",
                       f"install_task.ps1 devolvió código {rc}.")
            return False
        self._log("   OK — tarea programada registrada.")
        return True

    # --- Cierre limpio ----------------------------------------------------
    def _on_close(self) -> None:
        # No dejamos procesos colgando si se cierra a media instalación.
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
