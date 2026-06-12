"""
GUI moderna (customtkinter) que reemplaza los .bat del bot de ms_rewards.

Toda la lógica (rutas, comandos, formato de estado) vive en panel/core.py;
aquí solo construimos la interfaz, lanzamos subprocesos sin congelar la UI y
volcamos su salida en vivo a un panel de log.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading

import customtkinter as ctk
from tkinter import messagebox

# Import del core: como paquete (python -m panel.app) o suelto (exe empaquetado).
try:
    from panel import core
except ImportError:
    import core

try:
    from common import splash
except ImportError:
    splash = None


# --- Tema global ---------------------------------------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MONO = ("Consolas", 12)


class CredentialDialog(ctk.CTkToplevel):
    """Diálogo modal para pedir email + contraseña al cambiar de cuenta."""

    def __init__(self, master):
        super().__init__(master)
        self.title("Cambiar cuenta")
        self.geometry("380x230")
        self.resizable(False, False)
        self.result: tuple[str, str] | None = None

        ctk.CTkLabel(self, text="Datos de la cuenta de Microsoft",
                     font=("", 14, "bold")).pack(pady=(18, 12))

        ctk.CTkLabel(self, text="Email").pack(anchor="w", padx=24)
        self.email_entry = ctk.CTkEntry(self, width=320)
        self.email_entry.pack(padx=24, pady=(0, 10))

        ctk.CTkLabel(self, text="Contraseña").pack(anchor="w", padx=24)
        self.pass_entry = ctk.CTkEntry(self, width=320, show="•")
        self.pass_entry.pack(padx=24, pady=(0, 14))

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.pack()
        ctk.CTkButton(botones, text="Cancelar", width=120,
                      fg_color="gray30", command=self._cancel).pack(side="left", padx=6)
        ctk.CTkButton(botones, text="Aceptar", width=120,
                      command=self._accept).pack(side="left", padx=6)

        # Modal: capturar foco y bloquear la ventana principal.
        self.email_entry.focus()
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _accept(self):
        email = self.email_entry.get().strip()
        password = self.pass_entry.get()
        if not email or not password:
            messagebox.showwarning("Faltan datos",
                                   "Introduce email y contraseña.", parent=self)
            return
        self.result = (email, password)
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()


class UninstallDialog(ctk.CTkToplevel):
    """Diálogo modal con un checkbox por cada opción de desinstalación.

    `self.result` queda como la lista de claves marcadas si el usuario confirma,
    o None si cancela.
    """

    def __init__(self, master):
        super().__init__(master)
        self.title("Desinstalar")
        self.geometry("460x360")
        self.resizable(False, False)
        self.result: list[str] | None = None

        ctk.CTkLabel(self, text="¿Qué quieres eliminar?",
                     font=("", 14, "bold")).pack(pady=(18, 4))
        ctk.CTkLabel(
            self,
            text="⚠  Acción destructiva: lo seleccionado se borra de forma "
                 "permanente.",
            text_color="#e74c3c", wraplength=420, justify="left",
        ).pack(padx=24, pady=(0, 12))

        # Un checkbox por opción de core.UNINSTALL_OPTIONS.
        self._vars: list[tuple[str, ctk.BooleanVar]] = []
        for key, label, default in core.UNINSTALL_OPTIONS:
            var = ctk.BooleanVar(value=default)
            ctk.CTkCheckBox(self, text=label, variable=var).pack(
                anchor="w", padx=24, pady=4)
            self._vars.append((key, var))

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.pack(pady=(16, 0))
        ctk.CTkButton(botones, text="Cancelar", width=140,
                      fg_color="gray30", command=self._cancel).pack(side="left", padx=6)
        ctk.CTkButton(botones, text="Desinstalar", width=140,
                      fg_color="#c0392b", hover_color="#a93226",
                      command=self._accept).pack(side="left", padx=6)

        # Modal: capturar foco y bloquear la ventana principal.
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _accept(self):
        self.result = [key for key, var in self._vars if var.get()]
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ms_rewards — Panel de control")
        self.geometry("900x620")
        self.minsize(820, 560)
        try:
            ico = core.icon_path()
            if ico:
                self.iconbitmap(str(ico))
        except Exception:
            pass

        # Estado de ejecución de subprocesos.
        self.proc: subprocess.Popen | None = None
        self.out_queue: queue.Queue[str | None] = queue.Queue()
        self.action_buttons: list[ctk.CTkButton] = []
        self._venv_ok = core.venv_ready()

        self._build_layout()
        self._poll_queue()      # bombea la cola de salida hacia el textbox
        self.refresh_status()   # estado inicial al arrancar
        # Al cerrar la ventana, no dejar el subproceso (navegador/run.py) huérfano.
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Splash animado con el logo al abrir; al terminar reaparece la ventana.
        if splash is not None:
            splash.play(self, on_done=self._after_splash,
                        title="ms_rewards", subtitle="Panel de control")
        # Al abrir, comprobar/aplicar actualizaciones una vez (no bloqueante).
        # Se programa con after() para que la ventana aparezca primero y el
        # usuario vea el log "Buscar actualizaciones" llenándose.
        self.after(400, self._auto_update)

    # --- Construcción de la UI ------------------------------------------
    def _build_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Aviso si falta el venv (ocupa toda la fila superior).
        if not self._venv_ok:
            warn = ctk.CTkLabel(
                self,
                text="⚠  Ejecuta setup.exe primero — falta el entorno virtual",
                fg_color="#8e2b2b", corner_radius=8,
                text_color="white", font=("", 14, "bold"),
                height=36,
            )
            warn.grid(row=0, column=0, columnspan=2, sticky="ew",
                      padx=12, pady=(12, 0))

        self._build_status_panel()
        self._build_actions_panel()
        self._build_log_panel()

    def _build_status_panel(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=12)
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="Estado", font=("", 18, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        # Badge de color (etiqueta + fondo según status_style).
        self.badge = ctk.CTkLabel(frame, text="—", corner_radius=8,
                                  text_color="white", font=("", 13, "bold"),
                                  height=30)
        self.badge.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        # Resumen multilínea de format_status.
        self.status_label = ctk.CTkLabel(frame, text="", justify="left",
                                         anchor="nw", font=("Consolas", 12))
        self.status_label.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 10))
        frame.grid_rowconfigure(2, weight=1)

        ctk.CTkButton(frame, text="🔄 Actualizar estado",
                      command=self.refresh_status).grid(
            row=3, column=0, sticky="ew", padx=16, pady=(0, 6))

        # Nota: al abrir el panel se buscan actualizaciones automáticamente.
        ctk.CTkLabel(frame, text="ℹ Al abrir se buscan actualizaciones",
                     text_color="gray60", font=("", 11)).grid(
            row=4, column=0, sticky="w", padx=16, pady=(0, 14))

    def _build_actions_panel(self):
        frame = ctk.CTkScrollableFrame(self, label_text="Acciones")
        frame.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=12)
        frame.grid_columnconfigure(0, weight=1)

        # Botón por cada acción del bot.
        for action_id, action in core.ACTIONS.items():
            btn = ctk.CTkButton(
                frame, text=action.label,
                command=lambda a=action: self._on_action(a),
            )
            btn.pack(fill="x", padx=8, pady=5)
            self.action_buttons.append(btn)

        # Cambiar cuenta.
        btn = ctk.CTkButton(frame, text="👤 Cambiar cuenta",
                            command=self._on_switch_account)
        btn.pack(fill="x", padx=8, pady=5)
        self.action_buttons.append(btn)

        # Tarea programada.
        btn = ctk.CTkButton(frame, text="📅 Registrar tarea",
                            command=lambda: self._on_task(install=True))
        btn.pack(fill="x", padx=8, pady=5)
        self.action_buttons.append(btn)

        btn = ctk.CTkButton(frame, text="🗑 Quitar tarea", fg_color="gray30",
                            command=lambda: self._on_task(install=False))
        btn.pack(fill="x", padx=8, pady=5)
        self.action_buttons.append(btn)

        # Desinstalar (destructivo): se añade a action_buttons para que se
        # deshabilite sin venv y mientras corre otro proceso.
        btn = ctk.CTkButton(frame, text="🧹 Desinstalar",
                            fg_color="#c0392b", hover_color="#a93226",
                            command=self._on_uninstall)
        btn.pack(fill="x", padx=8, pady=5)
        self.action_buttons.append(btn)

        # Abrir logs: NO depende del venv, no se añade a action_buttons.
        ctk.CTkButton(frame, text="📂 Abrir carpeta de logs", fg_color="gray30",
                      command=self._open_logs).pack(fill="x", padx=8, pady=5)

        # Botón cancelar (oculto hasta que haya un proceso en marcha).
        self.cancel_btn = ctk.CTkButton(frame, text="■ Cancelar",
                                        fg_color="#c0392b", hover_color="#a93226",
                                        command=self._cancel_proc)

        # Si falta el venv, deshabilitar las acciones que lo usan.
        if not self._venv_ok:
            for b in self.action_buttons:
                b.configure(state="disabled")

    def _build_log_panel(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=2, column=0, columnspan=2, sticky="nsew",
                   padx=12, pady=(0, 12))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(frame, text="Log en vivo", font=("", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 4))

        self.log = ctk.CTkTextbox(frame, font=MONO, height=180,
                                  state="disabled", wrap="none")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

    # --- Estado ----------------------------------------------------------
    def refresh_status(self):
        """Relee last_run.json y la Scheduled Task; refresca badge y resumen."""
        last_run = core.read_last_run()
        task = self._query_task()

        label, color = core.status_style(last_run)
        self.badge.configure(text=f"  {label}  ", fg_color=color)
        self.status_label.configure(text=core.format_status(last_run, task))

    def _auto_update(self):
        """Al arrancar: comprueba/aplica actualizaciones (git pull) una vez.

        Reutiliza _launch (hilo + cola), así que no congela la UI. Degrada con
        gracia vía core.build_update_command (sin red / no es repo / sin versión
        nueva no falla). Si ya hay un proceso en marcha, no hace nada.
        """
        if self.proc is None and self._venv_ok:
            self._launch(core.build_update_command(),
                         title="Buscar actualizaciones")

    def _query_task(self) -> dict:
        """Consulta el estado de la Scheduled Task (síncrono, salida pequeña)."""
        try:
            out = subprocess.run(
                core.build_task_query_command(),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=15,
            )
            return core.parse_task_query(out.stdout)
        except Exception:
            return {}

    # --- Handlers de acciones -------------------------------------------
    def _on_action(self, action: core.Action):
        if action.confirm:
            if not messagebox.askyesno(
                    "Confirmar",
                    f"¿Seguro que quieres «{action.label.strip()}»?\n\n"
                    f"{action.description}"):
                return
        self._launch(core.build_run_command(action.id),
                     title=action.label.strip())

    def _on_switch_account(self):
        if not messagebox.askyesno(
                "Cambiar cuenta",
                "Se abrirá el navegador para iniciar sesión.\n"
                "El 2FA lo completas tú en esa ventana.\n\n¿Continuar?"):
            return
        dialog = CredentialDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        email, password = dialog.result
        # switch_account.py lee email (input) y contraseña (getpass) por stdin.
        self._launch(core.build_switch_command(), title="Cambiar cuenta",
                     stdin_data=f"{email}\n{password}\n")

    def _on_task(self, install: bool):
        title = "Registrar tarea" if install else "Quitar tarea"
        self._launch(core.build_task_command(install), title=title)

    def _on_uninstall(self):
        dialog = UninstallDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        opciones = dialog.result
        if not opciones:
            messagebox.showinfo(
                "Nada seleccionado",
                "No marcaste ninguna opción, así que no se borrará nada.")
            return
        # Confirmación final listando exactamente lo que se va a borrar.
        etiquetas = {key: label for key, label, _ in core.UNINSTALL_OPTIONS}
        listado = "\n".join(f"  • {etiquetas.get(o, o)}" for o in opciones)
        if not messagebox.askyesno(
                "Confirmar desinstalación",
                "Se va a eliminar de forma permanente:\n\n"
                f"{listado}\n\n¿Continuar?"):
            return
        self._launch(core.build_uninstall_command(opciones), title="Desinstalar")

    def _open_logs(self):
        try:
            core.LOG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(core.LOG_DIR))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n{exc}")

    # --- Ejecución de subprocesos ---------------------------------------
    def _launch(self, cmd: list[str], title: str, stdin_data: str | None = None):
        """Lanza un subproceso en segundo plano y vuelca su salida al log."""
        if self.proc is not None:
            messagebox.showinfo("Ocupado",
                                "Ya hay un proceso en marcha. Espera o cancélalo.")
            return

        self._set_actions_enabled(False)
        self._append_log(f"\n$ {' '.join(cmd)}\n")
        self._append_log(f"--- {title} ---\n")

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin_data is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(core.REWARDS_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW,
                bufsize=1,
            )
        except Exception as exc:
            self._append_log(f"[ERROR] No se pudo lanzar el proceso: {exc}\n")
            messagebox.showerror("Error", f"No se pudo lanzar el proceso:\n{exc}")
            self.proc = None
            self._set_actions_enabled(True)
            self.refresh_status()
            return

        # Mostrar el botón cancelar mientras corre.
        self.cancel_btn.pack(fill="x", padx=8, pady=(12, 5))

        # Hilo lector: vuelca stdin (si lo hay) y luego lee línea a línea.
        threading.Thread(target=self._reader, args=(self.proc, stdin_data),
                         daemon=True).start()

    def _reader(self, proc: subprocess.Popen, stdin_data: str | None):
        """Hilo daemon: escribe stdin y empuja la salida a la cola (no toca Tk)."""
        try:
            if stdin_data is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin_data)
                    proc.stdin.flush()
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.stdout is not None:
                for line in proc.stdout:
                    self.out_queue.put(line)
            proc.wait()
        except Exception as exc:
            self.out_queue.put(f"[ERROR lector] {exc}\n")
        finally:
            # Centinela: avisa a la UI de que el proceso terminó.
            self.out_queue.put(None)

    def _poll_queue(self):
        """Vacía la cola de salida en el hilo de la UI cada 100 ms."""
        try:
            while True:
                item = self.out_queue.get_nowait()
                if item is None:
                    self._on_proc_done()
                else:
                    self._append_log(item)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_proc_done(self):
        """Se ejecuta en la UI cuando el centinela llega: limpia y refresca."""
        code = self.proc.returncode if self.proc is not None else "?"
        self._append_log(f"--- proceso finalizado (código {code}) ---\n")
        self.proc = None
        self.cancel_btn.pack_forget()
        self._set_actions_enabled(True)
        self.refresh_status()

    def _cancel_proc(self):
        """Termina el proceso en marcha (proc.terminate)."""
        if self.proc is not None:
            try:
                self.proc.terminate()
                self._append_log("--- cancelando… ---\n")
            except Exception as exc:
                self._append_log(f"[ERROR] al cancelar: {exc}\n")

    def _after_splash(self):
        """Reaparece la ventana principal cuando el splash termina."""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _on_close(self):
        """Cierra la ventana terminando antes cualquier subproceso vivo."""
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.destroy()

    # --- Helpers de UI ---------------------------------------------------
    def _set_actions_enabled(self, enabled: bool):
        """Habilita/deshabilita los botones de acción (respeta falta de venv)."""
        if not self._venv_ok:
            enabled = False  # sin venv las acciones siguen bloqueadas
        state = "normal" if enabled else "disabled"
        for b in self.action_buttons:
            b.configure(state=state)

    def _append_log(self, text: str):
        """Añade texto al log y hace auto-scroll (solo desde el hilo de UI)."""
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")


if __name__ == "__main__":
    App().mainloop()
