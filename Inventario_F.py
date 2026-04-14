import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime
import json
import hashlib
import re
import copy
import threading
import requests  # Importante para Firebase

# URL de tu base de datos Firebase
FIREBASE_URL = "https://sistemasupermercado-f78dd-default-rtdb.firebaseio.com/"


# ==========================================
# VENTANA DE CARGA (INDICADOR DE PROGRESO)
# ==========================================
class VentanaCargando:
    """
    Ventana modal con barra de progreso indeterminada y mensaje personalizable.
    Bloquea la ventana padre mientras se ejecuta una operación de red en background.

    Uso:
        vc = VentanaCargando(parent, "Guardando en Firebase...")
        # ... lanzar thread ...
        vc.cerrar()  # llamar desde el hilo principal al terminar
    """
    def __init__(self, parent, mensaje="Conectando con el servidor..."):
        self.top = tk.Toplevel(parent)
        self.top.title("Por favor espere")
        self.top.geometry("340x110")
        self.top.resizable(False, False)
        self.top.grab_set()                      # Bloquea interacción con la ventana padre
        self.top.protocol("WM_DELETE_WINDOW", lambda: None)  # Deshabilitar el botón X

        # Centrar sobre el padre
        parent.update_idletasks()
        px = parent.winfo_x() + parent.winfo_width()  // 2 - 170
        py = parent.winfo_y() + parent.winfo_height() // 2 - 55
        self.top.geometry(f"340x110+{px}+{py}")

        tk.Label(self.top, text="⏳  " + mensaje,
                 font=("Arial", 10), pady=12).pack()

        self.barra = ttk.Progressbar(self.top, mode="indeterminate", length=280)
        self.barra.pack(pady=(0, 12))
        self.barra.start(12)   # Velocidad de animación en ms

        self.top.update()      # Forzar dibujado inmediato

    def cerrar(self):
        """Detiene la animación y destruye la ventana. Siempre llamar desde el hilo principal."""
        try:
            self.barra.stop()
            self.top.grab_release()
            self.top.destroy()
        except tk.TclError:
            pass  # Ya fue destruida


# ==========================================
# SISTEMA DE AUTENTICACIÓN
# ==========================================
class SistemaUsuarios:
    def __init__(self):
        self.usuarios = {}
        self.cargar_usuarios()
    
    def cargar_usuarios(self):
        """Carga usuarios desde Firebase con validación de estado"""
        try:
            response = requests.get(f"{FIREBASE_URL}usuarios.json", timeout=5)
            response.raise_for_status()
            
            data = response.json()
            if data:
                self.usuarios = data
            else:
                self.crear_usuarios_defecto()
        except Exception as e:
            print(f"Error crítico de conexión: {e}")
            self.usuarios = {}

    def guardar_usuarios(self):
        """Guarda usuarios en Firebase"""
        try:
            requests.put(f"{FIREBASE_URL}usuarios.json", json=self.usuarios)
        except Exception as e:
            print(f"Error guardando en la nube: {e}")

    def crear_usuarios_defecto(self):
        """Crea la estructura inicial de usuarios en Firebase si no existe"""
        self.usuarios = {
            "admin": {
                "password": self.hash_password("admin123"),
                "rol": "administrador",
                "nombre_completo": "Administrador"
            },
            "gerente": {
                "password": self.hash_password("gerente123"),
                "rol": "gerente",
                "nombre_completo": "Gerente de Tienda"
            },
            "empleado": {
                "password": self.hash_password("empleado123"),
                "rol": "empleado",
                "nombre_completo": "Empleado"
            }
        }
        self.guardar_usuarios()

    def hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()

    def validar_credenciales(self, usuario, password):
        if usuario not in self.usuarios:
            return False, None
        if self.usuarios[usuario]["password"] == self.hash_password(password):
            return True, self.usuarios[usuario]
        return False, None

    def agregar_usuario(self, usuario, password, rol, nombre_completo):
        if usuario in self.usuarios:
            return False, "El usuario ya existe"
        self.usuarios[usuario] = {
            "password": self.hash_password(password),
            "rol": rol,
            "nombre_completo": nombre_completo
        }
        self.guardar_usuarios()
        return True, "Usuario creado exitosamente"

    def restablecer_password(self, usuario_admin, password_admin, usuario_objetivo, password_nueva):
        """
        Permite a un administrador restablecer la contraseña de cualquier usuario.
        Requiere que el administrador se autentique con sus propias credenciales.
        """
        valido, datos_admin = self.validar_credenciales(usuario_admin, password_admin)
        if not valido or datos_admin.get("rol") != "administrador":
            return False, "Se requieren credenciales de administrador válidas"
        
        if usuario_objetivo not in self.usuarios:
            return False, f"El usuario '{usuario_objetivo}' no existe"
        
        if len(password_nueva) < 6:
            return False, "La nueva contraseña debe tener al menos 6 caracteres"
        
        self.usuarios[usuario_objetivo]["password"] = self.hash_password(password_nueva)
        self.guardar_usuarios()
        return True, f"Contraseña de '{usuario_objetivo}' restablecida correctamente"


# ==========================================
# CLASE DEL SISTEMA DE INVENTARIO
# ==========================================
class InventarioApp:
    CATEGORIAS = [
        "Lácteos", "Carnes y Embutidos", "Frutas y Verduras",
        "Panadería", "Abarrotes", "Bebidas", "Limpieza",
        "Cuidado Personal", "Congelados", "Otros"
    ]
    
    PERMISOS = {
        "administrador": ["crear", "editar", "eliminar", "ver", "reportes", "usuarios", "proveedores", "operar", "ventas"],
        "gerente":       ["crear", "editar", "ver", "reportes", "proveedores", "operar", "ventas"],
        "empleado":      ["ver", "operar", "ventas"] 
    }
    
    def __init__(self, root, usuario_data):
        self.root = root
        self.usuario_actual = usuario_data["usuario"]
        self.rol_actual = usuario_data["rol"]
        self.nombre_completo = usuario_data["nombre_completo"]
        
        self.root.title(f"Sistema de Inventario Supermercado - {self.nombre_completo} ({self.rol_actual.upper()})")
        self.root.geometry("1100x800")

        # Estado de sincronización: True mientras hay una operación de red activa
        self._sincronizando = False

        # Inicializar estructuras en memoria (se sobreescriben al cargar Firebase)
        self.productos             = {}
        self.proveedores           = {}
        self.historial_persistente = []
        self.producto_actual       = None
        self.tickets_abiertos = {1: {}}    # Empieza solo con el ID 1
        self.id_ticket_actual = 1          # El ticket que se ve en pantalla
        self.dict_tickets = {}    # Guardará los objetos de cada pestaña
        self.contador_tickets = 0
        # Construir la UI primero (ya con datos vacíos) y luego cargar en segundo plano
        self.setup_ui()
        self._cargar_datos_async()
    
    def tiene_permiso(self, accion):
        return accion in self.PERMISOS.get(self.rol_actual, [])

    # ── Validadores de entrada ─────────────────────────────────────────────
    def _validar_decimal(self, P):
        """Permite solo dígitos y un punto decimal (para precios)."""
        if P == "":
            return True
        return bool(re.match(r'^\d*\.?\d*$', P))

    def _validar_entero(self, P):
        """Permite solo dígitos enteros (para stock)."""
        if P == "":
            return True
        return P.isdigit()

    # ── Capa de red asíncrona ──────────────────────────────────────────────
    def _ejecutar_en_hilo(self, func_red, callback_exito, callback_error=None):
        """
        Ejecuta `func_red()` en un hilo de background.
        Cuando termina, llama a `callback_exito(resultado)` o
        `callback_error(excepcion)` en el hilo principal mediante root.after().

        Esto evita que Tkinter se congele ("No responde") durante llamadas de red.
        """
        def worker():
            try:
                resultado = func_red()
                self.root.after(0, lambda: callback_exito(resultado))
            except Exception as exc:
                if callback_error:
                    self.root.after(0, lambda e=exc: callback_error(e))
                else:
                    self.root.after(0, lambda e=exc: messagebox.showerror(
                        "Error de Red", f"Error inesperado: {e}"))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _cargar_datos_async(self):
        """Carga el inventario desde Firebase sin bloquear la UI."""
        vc = VentanaCargando(self.root, "Cargando inventario desde la nube...")

        def tarea_red():
            response = requests.get(f"{FIREBASE_URL}inventario.json", timeout=10)
            response.raise_for_status()
            return response.json()

        def al_terminar(data):
            vc.cerrar()
            if data:
                self.productos             = data.get("productos",  {}) or {}
                self.proveedores           = data.get("proveedores", {}) or {}
                hist                       = data.get("historial",   []) or []
                self.historial_persistente = hist if isinstance(hist, list) else list(hist.values())
            else:
                self.inicializar_datos_defecto()
            # Refrescar toda la UI con los datos ya cargados
            self.actualizar_vistas()
            if self.tiene_permiso("reportes"):
                self.actualizar_reportes()
            self._poblar_historial_inicial()
            if self.tiene_permiso("crear"):
                self._refrescar_combo_proveedor(self.combo_nuevo_prov)

        def al_error(exc):
            vc.cerrar()
            messagebox.showerror(
                "Error de Conexión",
                f"No se pudo conectar con Firebase.\n\n"
                f"Detalle: {exc}\n\n"
                "El sistema iniciará con datos vacíos.\n"
                "Los cambios no se sincronizarán hasta restablecer la conexión."
            )
            self.inicializar_datos_defecto()
            self.actualizar_vistas()
            self._poblar_historial_inicial()

        self._ejecutar_en_hilo(tarea_red, al_terminar, al_error)

    def _poblar_historial_inicial(self):
        """Rellena el widget de historial tras la carga inicial."""
        self.txt_historial.config(state="normal")
        self.txt_historial.delete("1.0", tk.END)
        entradas = (
            [e for e in self.historial_persistente if self.nombre_completo in e]
            if self.rol_actual == "empleado"
            else self.historial_persistente
        )
        for entrada in entradas[-100:]:
            self.txt_historial.insert(tk.END, entrada)
        self.txt_historial.config(state="disabled")

    def _guardar_datos_async(self, callback_post_guardado=None):
        """
        Guarda en Firebase en segundo plano.
        Mientras se guarda, `_sincronizando=True` bloquea re-entradas desde
        finalizar_operacion(). Al terminar, ejecuta `callback_post_guardado()`
        en el hilo principal mediante root.after().
        """
        if self._sincronizando:
            return  # Evitar guardados concurrentes (doble-clic)

        self._sincronizando = True

        # Deep copy mediante copy.deepcopy: garantiza que el hilo de red
        # trabaja con un snapshot inmutable del estado actual, sin importar
        # que el usuario siga operando en la UI mientras se envía a Firebase.
        data = {
            "productos":   copy.deepcopy(self.productos)             if self.productos             else {},
            "proveedores": copy.deepcopy(self.proveedores)           if self.proveedores           else {},
            "historial":   copy.deepcopy(self.historial_persistente) if self.historial_persistente else [],
        }

        vc = VentanaCargando(self.root, "Guardando cambios en la nube...")

        def tarea_red():
            response = requests.put(
                f"{FIREBASE_URL}inventario.json", json=data, timeout=10)
            response.raise_for_status()

        def al_terminar(_):
            vc.cerrar()
            self._sincronizando = False
            if callback_post_guardado:
                callback_post_guardado()

        def al_error(exc):
            vc.cerrar()
            self._sincronizando = False
            if isinstance(exc, requests.exceptions.Timeout):
                messagebox.showwarning(
                    "Conexión Lenta",
                    "El servidor tardó demasiado en responder.\n"
                    "Los datos se guardaron localmente pero pueden no haberse\n"
                    "sincronizado con Firebase."
                )
            else:
                messagebox.showerror(
                    "Error de Sincronización",
                    f"No se pudo guardar en Firebase.\n\nDetalle: {exc}"
                )
            # Aunque falle la red, ejecutar el callback para no dejar la UI colgada
            if callback_post_guardado:
                callback_post_guardado()

        self._ejecutar_en_hilo(tarea_red, al_terminar, al_error)

    def inicializar_datos_defecto(self):
        self.productos = {
            "Leche Entera 1L":       {"stock": 50,  "stock_minimo": 20, "precio_venta": 25.50, "precio_costo": 18.00, "categoria": "Lácteos",   "codigo_barras": "7501234567890"},
            "Arroz Blanco 1kg":      {"stock": 100, "stock_minimo": 30, "precio_venta": 35.00, "precio_costo": 25.00, "categoria": "Abarrotes",  "codigo_barras": "7501234567891"},
            "Aceite de Girasol 1L":  {"stock": 15,  "stock_minimo": 25, "precio_venta": 65.00, "precio_costo": 48.00, "categoria": "Abarrotes",  "codigo_barras": "7501234567892"},
            "Huevos (Docena)":       {"stock": 40,  "stock_minimo": 15, "precio_venta": 45.00, "precio_costo": 32.00, "categoria": "Lácteos",   "codigo_barras": "7501234567893"},
            "Pan de Molde":          {"stock": 10,  "stock_minimo": 15, "precio_venta": 38.00, "precio_costo": 25.00, "categoria": "Panadería",  "codigo_barras": "7501234567894"},
            "Detergente Líquido 1L": {"stock": 30,  "stock_minimo": 10, "precio_venta": 85.00, "precio_costo": 60.00, "categoria": "Limpieza",   "codigo_barras": "7501234567895"},
            "Café Molido 500g":      {"stock": 25,  "stock_minimo": 12, "precio_venta": 95.00, "precio_costo": 68.00, "categoria": "Bebidas",    "codigo_barras": "7501234567896"},
        }
        self.proveedores = {}
        self.historial_persistente = []

    def guardar_datos(self, callback=None):
        """
        Punto de entrada público para guardar.
        Delega a _guardar_datos_async para no bloquear la UI.
        `callback` se ejecuta en el hilo principal al terminar (éxito o error tolerado).
        """
        self._guardar_datos_async(callback_post_guardado=callback)

    # ──────────────────────────────────────────────────────────────────────
    # UI PRINCIPAL
    # ──────────────────────────────────────────────────────────────────────
    def setup_ui(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        menu_archivo = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Archivo", menu=menu_archivo)
        menu_archivo.add_command(label="Exportar Reporte", command=self.exportar_reporte)
        menu_archivo.add_separator()
        menu_archivo.add_command(label="Cerrar Sesión", command=self.cerrar_sesion)
        
        if self.tiene_permiso("usuarios"):
            menu_admin = tk.Menu(menubar, tearoff=0)
            menubar.add_cascade(label="Administración", menu=menu_admin)
            menu_admin.add_command(label="Gestionar Usuarios", command=self.ventana_usuarios)
            menu_admin.add_command(label="🔑 Gestionar Contraseñas", command=self.ventana_restablecer_password_admin)
        
        self.tab_control      = ttk.Notebook(self.root)
        self.tab_gestion      = ttk.Frame(self.tab_control)
        self.tab_stock        = ttk.Frame(self.tab_control)
        self.tab_bajo_stock   = ttk.Frame(self.tab_control)
        self.tab_proveedores  = ttk.Frame(self.tab_control)
        self.tab_reportes     = ttk.Frame(self.tab_control)
        self.tab_historial    = ttk.Frame(self.tab_control)
        self.tab_operaciones  = ttk.Frame(self.tab_control) 
        self.tab_pos = ttk.Frame(self.tab_control) # NUEVA

        if self.tiene_permiso("ventas"):
            self.tab_control.add(self.tab_pos, text="🛒 Punto de Venta")
            self.tab_control.select(self.tab_pos)
        if self.rol_actual in ["administrador", "gerente"]:
            self.tab_control.add(self.tab_gestion, text="⚙️ Configuración Maestra")
        if self.tiene_permiso("operar"):
            self.tab_control.add(self.tab_operaciones, text="📥 Añadir Stock")
        self.tab_control.add(self.tab_stock,      text="📊 Inventario General")
        self.tab_control.add(self.tab_bajo_stock, text="⚠️ Alertas de Stock")
        if self.tiene_permiso("proveedores"):
            self.tab_control.add(self.tab_proveedores, text="🏢 Proveedores")
        if self.tiene_permiso("reportes"):
            self.tab_control.add(self.tab_reportes, text="📈 Reportes")
        self.tab_control.add(self.tab_historial, text="📜 Historial")
        self.tab_control.pack(padx=10, pady=10, fill="both", expand=True)
        

        self.setup_tab_gestion()
        self.setup_tab_stock()
        self.setup_tab_bajo_stock()
        if self.tiene_permiso("proveedores"):
            self.setup_tab_proveedores()
        if self.tiene_permiso("reportes"):
            self.setup_tab_reportes()
        self.setup_tab_historial()
        if self.tiene_permiso("operar"):
            self.setup_tab_operaciones()
        if self.tiene_permiso("ventas"):
            self.setup_tab_pos()
        # Las vistas y el historial se poblarán en _cargar_datos_async()
        # una vez que Firebase responda.

# ──────────────────────────────────────────────────────────────────────
    # PESTAÑA: AÑADIR STOCK (SOLO ENTRADAS PARA EMPLEADOS)
    # ──────────────────────────────────────────────────────────────────────
    def setup_tab_operaciones(self):
        frame_p = tk.Frame(self.tab_operaciones, padx=40, pady=30)
        frame_p.pack(fill="both", expand=True)

        tk.Label(frame_p, text="📥 Registro de Entrada de Mercancía", font=("Arial", 14, "bold"), fg="#2E7D32").pack(anchor="w", pady=(0,20))
        
        tk.Label(frame_p, text="🔍 Buscar Producto (Nombre o Código):", font=("Arial", 11)).pack(anchor="w")
        self.entry_busq_op = tk.Entry(frame_p, font=("Arial", 12), width=50)
        self.entry_busq_op.pack(pady=5, anchor="w")
        self.entry_busq_op.bind("<KeyRelease>", self.controlar_autocompletado_op)
        
        # Sugerencias
        self.frame_lista_op = tk.Frame(frame_p, bd=1, relief="solid")
        self.lista_sugerencias_op = tk.Listbox(self.frame_lista_op, font=("Arial", 10), height=6)
        self.lista_sugerencias_op.pack(side="left", fill="both", expand=True)
        self.lista_sugerencias_op.bind("<Double-Button-1>", lambda e: self.seleccionar_op())

        # Info del producto seleccionado
        self.info_prod_op = tk.Label(frame_p, text="\nSeleccione un producto para añadir unidades", 
                                     font=("Arial", 10, "italic"), fg="gray", justify="left")
        self.info_prod_op.pack(pady=30, fill="x")

        # Contenedor de acción (Solo Añadir)
        self.frame_btns_op = tk.LabelFrame(frame_p, text=" Panel de Carga ", padx=20, pady=20)
        self.frame_btns_op.pack(fill="x")

        tk.Label(self.frame_btns_op, text="Cantidad a ingresar:", font=("Arial", 11)).grid(row=0, column=0, padx=10)
        self.ent_cant_op = tk.Entry(self.frame_btns_op, font=("Arial", 12, "bold"), width=12, state="disabled", justify="center")
        self.ent_cant_op.grid(row=0, column=1, padx=10)

        # Único botón disponible: AÑADIR
        self.btn_mas = tk.Button(self.frame_btns_op, text="✅ REGISTRAR ENTRADA", bg="#4CAF50", fg="white", 
                                 font=("Arial", 10, "bold"), width=25, height=2, state="disabled", 
                                 command=self.operacion_entrada_op)
        self.btn_mas.grid(row=0, column=2, padx=20)

    def controlar_autocompletado_op(self, event):
        texto = self.entry_busq_op.get().strip().lower()
        if not texto:
            self.frame_lista_op.pack_forget()
            return
        coincidencias = [n for n in self.productos.keys() if texto in n.lower() or texto in self.productos[n].get("codigo_barras", "")]
        if coincidencias:
            self.lista_sugerencias_op.delete(0, tk.END)
            for c in sorted(coincidencias): self.lista_sugerencias_op.insert(tk.END, c)
            self.frame_lista_op.pack(after=self.entry_busq_op, fill="x")
        else:
            self.frame_lista_op.pack_forget()

    def seleccionar_op(self):
        if not self.lista_sugerencias_op.curselection(): return
        nombre = self.lista_sugerencias_op.get(self.lista_sugerencias_op.curselection()[0])
        
        self.producto_confirmado_op = nombre 
        self.producto_actual = nombre
        
        self.entry_busq_op.delete(0, tk.END)
        self.entry_busq_op.insert(0, nombre)
        self.frame_lista_op.pack_forget()
        
        p = self.productos[nombre]
        self.info_prod_op.config(text=f"📦 PRODUCTO: {nombre}\nStock Actual: {p['stock']} unidades\nCategoría: {p.get('categoria','N/A')}", 
                                 font=("Arial", 11, "bold"), fg="#2E7D32")
        self.ent_cant_op.config(state="normal")
        self.ent_cant_op.delete(0, tk.END)
        self.btn_mas.config(state="normal")

    def operacion_entrada_op(self):
        # 1. Obtenemos lo que está escrito actualmente en la búsqueda
        texto_en_pantalla = self.entry_busq_op.get().strip()
        
        # 2. Comparamos con el producto que realmente se seleccionó
        if texto_en_pantalla != getattr(self, 'producto_confirmado_op', ''):
            messagebox.showerror("Error de Seguridad", 
                                 "El nombre del producto no coincide con la selección original.\n\n"
                                 "Por favor, seleccione el producto nuevamente de la lista.")
            self.limpiar_tras_operacion()
            return

        # 3. Si todo está bien, procedemos con la carga normal
        self.entry_cantidad_op = self.ent_cant_op
        self.operacion_entrada()
        self.root.after(500, self.limpiar_tras_operacion)

    def limpiar_tras_operacion(self):
        self.entry_busq_op.delete(0, tk.END)
        self.ent_cant_op.delete(0, tk.END)
        self.ent_cant_op.config(state="disabled")
        self.btn_mas.config(state="disabled")
        self.info_prod_op.config(text="\n✅ Entrada registrada con éxito. Seleccione otro producto.", fg="#2E7D32")
        if hasattr(self, 'frame_sug_pos'):
            self.frame_sug_pos.place_forget()
        
    def verificar_clic_fuera_sugerencias(self, event):
        """Oculta la lista si el usuario hace clic fuera, pero ignora si hace clic en el buscador actual"""
        try:
            # Buscamos si el widget clickeado es un Entry (buscador)
            es_buscador = isinstance(event.widget, tk.Entry)
            
            # Si el clic NO es en la lista Y NO es en un buscador, cerramos
            if event.widget != self.list_sug_pos and not es_buscador:
                self.frame_sug_pos.place_forget()
                self.root.unbind_all("<Button-1>")
        except:
            pass

    def setup_tab_pos(self):
        # Frame contenedor principal de la pestaña POS
        frame_pos_principal = tk.Frame(self.tab_pos)
        frame_pos_principal.pack(fill="both", expand=True)

        # 1. Botones superiores
        frame_superior = tk.Frame(frame_pos_principal, padx=10, pady=5)
        frame_superior.pack(fill="x")

        tk.Button(frame_superior, text="➕ Nuevo Ticket (F4)", bg="#2196F3", fg="white", 
                  font=("Arial", 9, "bold"), command=self.añadir_nuevo_ticket_dinamico).pack(side="left")
        
        tk.Button(frame_superior, text="🗑️ Eliminar Ticket Actual", bg="#B71C1C", fg="white", 
                  font=("Arial", 9, "bold"), command=self.cerrar_ticket_activo).pack(side="left", padx=10)

        # 2. El Notebook de pestañas
        self.notebook_tickets = ttk.Notebook(frame_pos_principal)
        self.notebook_tickets.pack(fill="both", expand=True, padx=10, pady=5)

        # 3. Teclas rápidas
        self.root.bind("<F4>", lambda e: self.añadir_nuevo_ticket_dinamico())
        self.root.bind("<F5>", lambda e: self.finalizar_venta_teclado())

        # 4. Lista de sugerencias global
        self.frame_sug_pos = tk.Frame(self.root, bd=1, relief="solid", bg="white")
        self.list_sug_pos = tk.Listbox(self.frame_sug_pos, font=("Arial", 10), height=8)
        self.list_sug_pos.pack(fill="both", expand=True)
        self.frame_sug_pos.place_forget()

        # 5. Abrir el primer ticket
        self.añadir_nuevo_ticket_dinamico()
    
    
    def controlar_autocompletado_pos_dinamico(self, event, id_t):
        ent = self.dict_tickets[id_t]["ent_busq"]
        texto = ent.get().strip().lower()
        
        # Si la caja está vacía, ocultamos y salimos
        if not texto:
            self.frame_sug_pos.place_forget()
            return

        # Buscamos coincidencias
        coincidencias = [n for n in self.productos.keys() if texto in n.lower() or texto in self.productos[n].get("codigo_barras", "")]
        
        if coincidencias:
            self.list_sug_pos.delete(0, tk.END)
            for c in sorted(coincidencias): 
                self.list_sug_pos.insert(tk.END, c)
            
            self.list_sug_pos.bind("<Double-Button-1>", lambda e: self.seleccionar_para_carrito_dinamico(id_t))
            
            # Posicionamiento (Debajo del Entry)
            x_pos = ent.winfo_rootx() - self.root.winfo_rootx()
            y_pos = ent.winfo_rooty() - self.root.winfo_rooty() + ent.winfo_height() + 2
            
            self.frame_sug_pos.place(x=x_pos, y=y_pos, width=ent.winfo_width())
            self.frame_sug_pos.lift()
            
            # Reactivamos el "escucha" para cerrar al hacer clic fuera
            self.root.bind_all("<Button-1>", self.verificar_clic_fuera_sugerencias)
        else:
            self.frame_sug_pos.place_forget()

    def seleccionar_para_carrito_dinamico(self, id_t):
        if not self.list_sug_pos.curselection(): return
        nombre = self.list_sug_pos.get(self.list_sug_pos.curselection()[0])
        self.agregar_item_logica_dinamica(nombre, id_t)
        
        self.dict_tickets[id_t]["ent_busq"].delete(0, tk.END)
        self.frame_sug_pos.place_forget()
        self.root.unbind_all("<Button-1>") # Limpiamos el evento
        self.dict_tickets[id_t]["ent_busq"].focus()

    def agregar_item_logica_dinamica(self, nombre, id_t):
        carrito = self.dict_tickets[id_t]["carrito"]
        p_data = self.productos[nombre]
        
        if p_data['stock'] <= 0:
            messagebox.showwarning("Sin Stock", f"El producto {nombre} está agotado.")
            return

        if nombre in carrito:
            carrito[nombre]['cant'] += 1
        else:
            carrito[nombre] = {'cant': 1, 'precio': p_data['precio_venta']}
        
        self.actualizar_tabla_pos_dinamica(id_t)

    def actualizar_tabla_pos_dinamica(self, id_t):
        ticket = self.dict_tickets[id_t]
        tree = ticket["tree"]
        carrito = ticket["carrito"]
        
        for item in tree.get_children(): tree.delete(item)
        
        total = 0
        for nombre, d in carrito.items():
            subtotal = d['cant'] * d['precio']
            total += subtotal
            cod = self.productos[nombre].get('codigo_barras', 'N/A')
            tree.insert("", tk.END, values=(cod, d['cant'], nombre, f"${d['precio']:.2f}", f"${subtotal:.2f}"))
            
        ticket["lbl_total"].config(text=f"${total:,.2f}")

    def quitar_del_carrito(self):
        sel = self.tree_pos.selection()
        if not sel: return
        nombre = self.tree_pos.item(sel[0])['values'][1]
        del self.carrito_items[nombre]
        self.actualizar_tabla_pos()

    def añadir_nuevo_ticket_dinamico(self):
        self.contador_tickets += 1
        id_t = self.contador_tickets
        
        tab_ticket = ttk.Frame(self.notebook_tickets)
        self.notebook_tickets.add(tab_ticket, text=f"Ticket {id_t}   ")
        self.notebook_tickets.select(tab_ticket)

        frame_cuerpo = tk.Frame(tab_ticket, padx=20, pady=10)
        frame_cuerpo.pack(fill="both", expand=True)

        # Buscador específico de este ticket
        tk.Label(frame_cuerpo, text="🔍 Escanear o Buscar:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_busq = tk.Entry(frame_cuerpo, font=("Arial", 14), width=40)
        ent_busq.pack(pady=5, anchor="w")
        
        # Tabla (Treeview)
        cols = ("Cod", "Cant", "Producto", "Precio U.", "Subtotal")
        tree = ttk.Treeview(frame_cuerpo, columns=cols, show="headings", height=12)
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=100, anchor="center")
        tree.column("Producto", width=250, anchor="w")
        tree.pack(side="left", fill="both", expand=True, pady=10)

        # Panel lateral de cobro
        # Panel lateral de cobro
        frame_pago = tk.Frame(frame_cuerpo, padx=20)
        frame_pago.pack(side="right", fill="y")

        lbl_total = tk.Label(frame_pago, text="$0.00", font=("Arial", 30, "bold"), fg="#2E7D32")
        lbl_total.pack(pady=20)

        # Botón Cobrar
        btn_cobrar = tk.Button(frame_pago, text="💳 COBRAR (F5)", bg="#4CAF50", fg="white", 
                               font=("Arial", 12, "bold"), width=18, height=3,
                               command=lambda: self.finalizar_venta_dinamica(id_t))
        btn_cobrar.pack(pady=10)

        # NUEVO: Botón Quitar Item Seleccionado
        tk.Button(frame_pago, text="❌ Quitar Artículo", bg="#FF9800", fg="white", 
                  width=18, height=1, font=("Arial", 9),
                  command=lambda: self.quitar_item_dinamico(id_t)).pack(pady=5)

        # NUEVO: Botón Vaciar Carrito Completo
        tk.Button(frame_pago, text="🗑️ Vaciar Carrito", bg="#607D8B", fg="white", 
                  width=18, height=1, font=("Arial", 9),
                  command=lambda: self.vaciar_carrito_dinamico(id_t)).pack(pady=5)

        # Guardar referencias en el diccionario maestro
        self.dict_tickets[id_t] = {
            "tab": tab_ticket,
            "tree": tree,
            "ent_busq": ent_busq,
            "lbl_total": lbl_total,
            "carrito": {}
        }

        # Vincular autocompletado
        ent_busq.bind("<KeyRelease>", lambda e, i=id_t: self.controlar_autocompletado_pos_dinamico(e, i))
        ent_busq.bind("<Button-1>", lambda e, i=id_t: self.controlar_autocompletado_pos_dinamico(e, i))
        ent_busq.focus_set()

    def cerrar_ticket_especifico(self, tab, id_t):
        if messagebox.askyesno("Cerrar", f"¿Desea cerrar el Ticket {id_t}?\nSe perderán los productos no cobrados."):
            self.notebook_tickets.forget(tab)
            del self.dict_tickets[id_t]

    def cambiar_ticket(self, event):
        """Guarda el progreso del ticket actual y carga el seleccionado"""
        # Guardar lo que hay en el carrito actual antes de movernos
        self.tickets_abiertos[self.id_ticket_actual] = self.carrito_items
        
        try:
            # Obtener el ID del combo y cargar sus productos
            nuevo_id = int(self.combo_tickets.get())
            self.id_ticket_actual = nuevo_id
            self.carrito_items = self.tickets_abiertos.get(nuevo_id, {})
            self.actualizar_tabla_pos()
        except Exception as e:
            print(f"Error al cambiar ticket: {e}")

    def quitar_item_dinamico(self, id_t):
        """Elimina solo el producto seleccionado en la tabla del ticket actual"""
        tree = self.dict_tickets[id_t]["tree"]
        carrito = self.dict_tickets[id_t]["carrito"]
        
        seleccion = tree.selection()
        if not seleccion:
            messagebox.showwarning("Atención", "Seleccione un artículo para quitar.")
            return
            
        # Obtenemos el nombre del producto de la columna 2 (índice 2)
        nombre_prod = tree.item(seleccion[0])['values'][2]
        
        if nombre_prod in carrito:
            del carrito[nombre_prod]
            self.actualizar_tabla_pos_dinamica(id_t)

    def vaciar_carrito_dinamico(self, id_t):
        """Borra todos los productos del carrito actual"""
        if not self.dict_tickets[id_t]["carrito"]: return
        
        if messagebox.askyesno("Confirmar", "¿Vaciar toda la lista del ticket actual?"):
            self.dict_tickets[id_t]["carrito"] = {}
            self.actualizar_tabla_pos_dinamica(id_t)

    def cerrar_ticket_activo(self):
        """Detecta la pestaña actual y la cierra"""
        try:
            idx = self.notebook_tickets.index("current")
            tab_text = self.notebook_tickets.tab(idx, "text")
            # Extraer el ID del texto "Ticket X"
            id_t = int(re.search(r'\d+', tab_text).group())
            
            # Usamos la función que ya teníamos para cerrar
            tab_obj = self.dict_tickets[id_t]["tab"]
            self.cerrar_ticket_especifico(tab_obj, id_t)
        except Exception as e:
            print(f"Error al cerrar ticket: {e}")

    def vaciar_carrito_completo(self):
        """Limpia el ticket actual tanto en pantalla como en memoria"""
        if not self.carrito_items: return
        if messagebox.askyesno("Confirmar", f"¿Desea borrar todos los productos del Ticket #{self.id_ticket_actual}?"):
            self.carrito_items = {}
            self.tickets_abiertos[self.id_ticket_actual] = {}
            self.actualizar_tabla_pos()

    def finalizar_venta_teclado(self):
        """Detecta qué pestaña está activa cuando presionas F5"""
        try:
            idx = self.notebook_tickets.index("current")
            tab_text = self.notebook_tickets.tab(idx, "text")
            id_t = int(re.search(r'\d+', tab_text).group())
            self.finalizar_venta_dinamica(id_t)
        except:
            pass

    def finalizar_venta_dinamica(self, id_t):
        """Lógica para el botón de cada ticket"""
        ticket = self.dict_tickets[id_t]
        carrito = ticket["carrito"]
        
        if not carrito:
            messagebox.showwarning("Carrito Vacío", "No hay productos en este ticket.")
            return

        total_venta = sum(d['cant'] * d['precio'] for d in carrito.values())

        # Ventana para cobrar
        ventana_pago = tk.Toplevel(self.root)
        ventana_pago.title(f"Cobrar Ticket {id_t}")
        ventana_pago.geometry("300x300")
        ventana_pago.grab_set()

        tk.Label(ventana_pago, text=f"TOTAL: ${total_venta:,.2f}", 
                 font=("Arial", 16, "bold"), fg="#2E7D32").pack(pady=15)
        
        tk.Label(ventana_pago, text="Dinero Recibido:").pack()
        ent_pago = tk.Entry(ventana_pago, font=("Arial", 14), justify="center")
        ent_pago.pack(pady=10)
        ent_pago.focus_set()

        def procesar_pago(event=None):
            try:
                pago_cliente = float(ent_pago.get())
                if pago_cliente < total_venta:
                    messagebox.showerror("Error", "Dinero insuficiente.", parent=ventana_pago)
                    return
                
                cambio = pago_cliente - total_venta
                
                # Descontar del inventario real
                detalles_historial = []
                for nombre, d in carrito.items():
                    self.productos[nombre]['stock'] -= d['cant']
                    detalles_historial.append(f"{nombre} (x{d['cant']})")
                
                self.registrar_historial(f"VENTA Ticket {id_t}: {', '.join(detalles_historial)} | TOTAL: ${total_venta:.2f}")
                
                # Guardar en Firebase y limpiar
                self.guardar_datos(callback=lambda: self.finalizar_exito_dinamico(ventana_pago, cambio, id_t))

            except ValueError:
                messagebox.showerror("Error", "Ingrese un monto válido.", parent=ventana_pago)

        ventana_pago.bind("<Return>", procesar_pago)
        ventana_pago.bind("<F5>", procesar_pago)
        
        tk.Button(ventana_pago, text="CONFIRMAR PAGO (F5)", bg="#4CAF50", fg="white", 
                  font=("Arial", 10, "bold"), command=procesar_pago, height=2, width=20).pack(pady=10)

    def finalizar_exito_dinamico(self, ventana_pago, cambio, id_t):
        ventana_pago.destroy()
        messagebox.showinfo("Venta Exitosa", f"CAMBIO A DEVOLVER: ${cambio:,.2f}")
        
        # Limpiar el carrito de esa pestaña específica
        self.dict_tickets[id_t]["carrito"] = {}
        self.actualizar_tabla_pos_dinamica(id_t)
        self.actualizar_vistas()

    # ──────────────────────────────────────────────────────────────────────
    # PESTAÑA GESTIÓN DE PRODUCTOS
    # ──────────────────────────────────────────────────────────────────────
    
    def setup_tab_gestion(self):
        canvas_gestion   = tk.Canvas(self.tab_gestion)
        scrollbar_gestion = ttk.Scrollbar(self.tab_gestion, orient="vertical", command=canvas_gestion.yview)
        scrollable_frame  = tk.Frame(canvas_gestion)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas_gestion.configure(scrollregion=canvas_gestion.bbox("all"))
        )
        canvas_gestion.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas_gestion.configure(yscrollcommand=scrollbar_gestion.set)
        canvas_gestion.pack(side="left", fill="both", expand=True)
        scrollbar_gestion.pack(side="right", fill="y")
        
        def _on_mousewheel(event):
            canvas_gestion.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas_gestion.bind_all("<MouseWheel>", _on_mousewheel)

        # ── BUSCADOR ──────────────────────────────────────────────────────
        self.frame_busqueda = tk.LabelFrame(scrollable_frame,
                                            text="🔎 Buscar y Filtrar Productos",
                                            padx=20, pady=10)
        self.frame_busqueda.pack(pady=5, padx=20, fill="x")

        # Fila 1: texto + botones
        frame_busq_principal = tk.Frame(self.frame_busqueda)
        frame_busq_principal.pack(fill="x", pady=(0, 8))

        tk.Label(frame_busq_principal, text="🔍 Nombre/Código:").pack(side="left", padx=5)
        self.entry_busqueda = tk.Entry(frame_busq_principal, font=("Arial", 11), width=35)
        self.entry_busqueda.pack(side="left", padx=5)
        # Al escribir → solo sugerencias, NUNCA carga automáticamente
        self.entry_busqueda.bind("<KeyRelease>", self.controlar_autocompletado)
        # Enter o botón Buscar → búsqueda confirmada
        self.entry_busqueda.bind("<Return>", lambda e: self.buscar_producto())

        tk.Button(frame_busq_principal, text="🔎 Buscar",
                  command=self.buscar_producto, bg="#4CAF50", fg="white").pack(side="left", padx=5)
        tk.Button(frame_busq_principal, text="🔄 Limpiar",
                  command=self.limpiar_filtros, bg="#ff9800", fg="white").pack(side="left", padx=2)

        # Fila 2: categoría y estado de stock
        frame_filtros_1 = tk.Frame(self.frame_busqueda)
        frame_filtros_1.pack(fill="x", pady=(0, 8))

        tk.Label(frame_filtros_1, text="📁 Categoría:").pack(side="left", padx=5)
        self.combo_filtro_categ = ttk.Combobox(
            frame_filtros_1, values=["Todas"] + self.CATEGORIAS,
            state="readonly", width=17)
        self.combo_filtro_categ.pack(side="left", padx=5)
        self.combo_filtro_categ.current(0)
        # Cambiar categoría → refresca sugerencias si hay texto; NO carga producto
        self.combo_filtro_categ.bind("<<ComboboxSelected>>",
                                     lambda e: self._actualizar_sugerencias_por_filtro())

        tk.Label(frame_filtros_1, text="📊 Estado Stock:").pack(side="left", padx=5)
        self.combo_estado_stock = ttk.Combobox(
            frame_filtros_1,
            values=["Todos", "Bajo Stock", "Stock Normal", "Stock Alto"],
            state="readonly", width=17)
        self.combo_estado_stock.pack(side="left", padx=5)
        self.combo_estado_stock.current(0)
        # Igual: solo refresca sugerencias
        self.combo_estado_stock.bind("<<ComboboxSelected>>",
                                     lambda e: self._actualizar_sugerencias_por_filtro())

        # Fila 3: rango de precios
        frame_filtros_2 = tk.Frame(self.frame_busqueda)
        frame_filtros_2.pack(fill="x")

        tk.Label(frame_filtros_2, text="💵 Rango de Precio:").pack(side="left", padx=5)
        tk.Label(frame_filtros_2, text="De:").pack(side="left", padx=2)
        self.entry_precio_min = tk.Entry(frame_filtros_2, font=("Arial", 10), width=10)
        self.entry_precio_min.pack(side="left", padx=2)
        self.entry_precio_min.insert(0, "0")
        # Precio solo se aplica al pulsar Buscar, no en tiempo real
        tk.Label(frame_filtros_2, text="Hasta:").pack(side="left", padx=2)
        self.entry_precio_max = tk.Entry(frame_filtros_2, font=("Arial", 10), width=10)
        self.entry_precio_max.pack(side="left", padx=2)
        self.entry_precio_max.insert(0, "9999")

        # ── Lista de sugerencias con scrollbar ───────────────────────────
        # Wrapper oculto: se muestra solo cuando hay sugerencias
        self.frame_lista_wrap = tk.Frame(self.frame_busqueda, bd=1, relief="solid")

        self.lista_sugerencias = tk.Listbox(
            self.frame_lista_wrap,
            font=("Arial", 10),
            exportselection=False,
            activestyle="dotbox",
            selectmode=tk.SINGLE
        )
        sb_lista = ttk.Scrollbar(self.frame_lista_wrap, orient="vertical",
                                  command=self.lista_sugerencias.yview)
        self.lista_sugerencias.configure(yscrollcommand=sb_lista.set)
        self.lista_sugerencias.pack(side="left", fill="both", expand=True)
        sb_lista.pack(side="right", fill="y")

        # Doble clic o Enter en la lista → seleccionar y cargar
        self.lista_sugerencias.bind("<Double-Button-1>", lambda e: self.seleccionar_de_lista())
        self.lista_sugerencias.bind("<Return>",          lambda e: self.seleccionar_de_lista())

        # ── Panel info del producto ───────────────────────────────────────
        self.frame_info = tk.LabelFrame(scrollable_frame,
                                        text="📦 Información del Producto",
                                        padx=20, pady=10)
        self.frame_info.pack(pady=5, padx=20, fill="x")

        self.lbl_producto_info = tk.Label(
            self.frame_info,
            text="Seleccione un producto para ver detalles",
            font=("Arial", 10, "italic"), fg="gray")
        self.lbl_producto_info.pack(pady=10)

        # ── Panel de edición ──────────────────────────────────────────────
        if self.tiene_permiso("editar"):
            self.frame_operaciones = tk.LabelFrame(scrollable_frame,
                                                   text="⚙️ Panel de Control",
                                                   padx=20, pady=10)
            self.frame_operaciones.pack(pady=5, padx=20, fill="x")

            frame_stock_edit = tk.LabelFrame(self.frame_operaciones, text="Stock", padx=10, pady=5)
            frame_stock_edit.pack(fill="x", pady=5)
            frame_sc = tk.Frame(frame_stock_edit)
            frame_sc.pack()

            tk.Label(frame_sc, text="Cantidad:").grid(row=0, column=0, padx=5)
            vcmd_op = (self.root.register(self._validar_entero), '%P')
            self.entry_cantidad_op = tk.Entry(frame_sc, font=("Arial", 11), width=10, state="disabled",
                                              validate="key", validatecommand=vcmd_op)
            self.entry_cantidad_op.grid(row=0, column=1, padx=5)

            self.btn_entrada = tk.Button(frame_sc, text="➕ Entrada", command=self.operacion_entrada,
                                         bg="#4CAF50", fg="white", state="disabled", width=12)
            self.btn_entrada.grid(row=0, column=2, padx=5)

            self.btn_salida = tk.Button(frame_sc, text="➖ Salida", command=self.operacion_salida,
                                        bg="#f44336", fg="white", state="disabled", width=12)
            self.btn_salida.grid(row=0, column=3, padx=5)

            frame_ajuste = tk.LabelFrame(self.frame_operaciones,
                                         text="Ajuste Manual de Stock", padx=10, pady=5)
            frame_ajuste.pack(fill="x", pady=5)

            frame_ajuste_controles = tk.Frame(frame_ajuste)
            frame_ajuste_controles.pack(fill="x")

            tk.Label(frame_ajuste_controles, text="Nuevo Stock Total:").pack(side="left", padx=5)
            self.val_stock = tk.IntVar()
            self.spin_stock = tk.Spinbox(frame_ajuste_controles, from_=0, to=99999,
                                          textvariable=self.val_stock, font=("Arial", 11),
                                          width=10, state="disabled")
            self.spin_stock.pack(side="left", padx=5)
            self.btn_ajuste_stock = tk.Button(frame_ajuste_controles, text="Aplicar Ajuste",
                                              command=self.ajuste_manual_stock,
                                              bg="#FF9800", state="disabled")
            self.btn_ajuste_stock.pack(side="left", padx=10)

            frame_ajuste_desc = tk.Frame(frame_ajuste)
            frame_ajuste_desc.pack(fill="x", pady=(6, 2))
            tk.Label(frame_ajuste_desc, text="Descripción / Motivo del ajuste  * (obligatorio)",
                     font=("Arial", 9, "bold"), fg="#c62828").pack(anchor="w", padx=5)
            self.entry_ajuste_desc = tk.Entry(frame_ajuste_desc, font=("Arial", 10),
                                               width=55, state="disabled", bg="#fff8e1")
            self.entry_ajuste_desc.pack(fill="x", padx=5, pady=(2, 4))

            frame_precios = tk.LabelFrame(self.frame_operaciones,
                                          text="Precios y Configuración", padx=10, pady=5)
            frame_precios.pack(fill="x", pady=5)

            frame_nombre = tk.Frame(frame_precios)
            frame_nombre.pack(fill="x", pady=(0, 8))
            tk.Label(frame_nombre, text="📝 Nombre del Producto:",
                     font=("Arial", 9, "bold")).pack(anchor="w", padx=5, pady=(5, 2))
            self.entry_nombre_producto = tk.Entry(frame_nombre, font=("Arial", 11),
                                                   state="disabled", bg="#f5f5f5")
            self.entry_nombre_producto.pack(fill="x", padx=5, pady=(0, 5))

            frame_precio_grid = tk.Frame(frame_precios)
            frame_precio_grid.pack()

            vcmd_dec = (self.root.register(self._validar_decimal), '%P')
            vcmd_int = (self.root.register(self._validar_entero), '%P')

            tk.Label(frame_precio_grid, text="Precio Venta:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
            self.entry_precio_venta = tk.Entry(frame_precio_grid, font=("Arial", 10), width=12, state="disabled", validate="key", validatecommand=vcmd_dec)
            self.entry_precio_venta.grid(row=0, column=1, padx=5, pady=2)

            tk.Label(frame_precio_grid, text="Precio Costo:").grid(row=0, column=2, sticky="w", padx=5, pady=2)
            self.entry_precio_costo = tk.Entry(frame_precio_grid, font=("Arial", 10), width=12, state="disabled", validate="key", validatecommand=vcmd_dec)
            self.entry_precio_costo.grid(row=0, column=3, padx=5, pady=2)

            tk.Label(frame_precio_grid, text="Stock Mínimo:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
            self.entry_stock_min = tk.Entry(frame_precio_grid, font=("Arial", 10), width=12, state="disabled", validate="key", validatecommand=vcmd_int)
            self.entry_stock_min.grid(row=1, column=1, padx=5, pady=2)

            tk.Label(frame_precio_grid, text="Cód. Barras:").grid(row=1, column=2, sticky="w", padx=5, pady=2)
            self.entry_edit_codigo = tk.Entry(frame_precio_grid, font=("Arial", 10), width=15, state="disabled")
            self.entry_edit_codigo.grid(row=1, column=3, padx=5, pady=2)

            tk.Label(frame_precio_grid, text="Departamento:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
            self.combo_edit_cat = ttk.Combobox(frame_precio_grid, values=self.CATEGORIAS, state="disabled", width=18)
            self.combo_edit_cat.grid(row=2, column=1, padx=5, pady=2, sticky="w")

            tk.Label(frame_precio_grid, text="Proveedor:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
            self.combo_edit_prov = ttk.Combobox(frame_precio_grid, state="readonly", width=28)
            self.combo_edit_prov.grid(row=3, column=1, columnspan=3, padx=5, pady=2, sticky="w")

            tk.Label(frame_precio_grid, text="Cód. Barras:").grid(row=1, column=2, sticky="w", padx=5, pady=2)
            self.entry_edit_codigo = tk.Entry(frame_precio_grid, font=("Arial", 10), width=15, state="disabled")
            self.entry_edit_codigo.grid(row=1, column=3, padx=5, pady=2)

            tk.Label(frame_precio_grid, text="Departamento:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
            self.combo_edit_cat = ttk.Combobox(frame_precio_grid, values=self.CATEGORIAS, state="disabled", width=18)
            self.combo_edit_cat.grid(row=2, column=1, padx=5, pady=2, sticky="w")

            self.btn_guardar_config = tk.Button(frame_precio_grid, text="💾 Guardar Cambios",
                                                command=self.guardar_configuracion_producto,
                                                bg="#2196F3", fg="white", state="disabled", font=("Arial", 10, "bold"))
            self.btn_guardar_config.grid(row=4, column=0, columnspan=4, pady=15)
            if self.tiene_permiso("eliminar"):
                self.btn_eliminar = tk.Button(self.frame_operaciones, text="🗑️ Eliminar Producto",
                                              command=self.eliminar_producto, bg="#B71C1C", fg="white",
                                              state="disabled", font=("Arial", 10, "bold"))
                self.btn_eliminar.pack(pady=10)

        # ── Registro de nuevo producto ────────────────────────────────────
        if self.tiene_permiso("crear"):
            self.frame_alta = tk.LabelFrame(scrollable_frame, text="➕ Registrar Nuevo Producto",
                                            padx=20, pady=10)
            self.frame_alta.pack(pady=10, padx=20, fill="x")

            frame_grid = tk.Frame(self.frame_alta)
            frame_grid.pack()

            vcmd_dec2 = (self.root.register(self._validar_decimal), '%P')
            vcmd_int2 = (self.root.register(self._validar_entero), '%P')

            tk.Label(frame_grid, text="Nombre:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
            self.entry_nuevo_nom = tk.Entry(frame_grid, font=("Arial", 10), width=25)
            self.entry_nuevo_nom.grid(row=0, column=1, padx=5, pady=3)

            tk.Label(frame_grid, text="Código Barras:").grid(row=0, column=2, sticky="w", padx=5, pady=3)
            self.entry_nuevo_codigo = tk.Entry(frame_grid, font=("Arial", 10), width=15)
            self.entry_nuevo_codigo.grid(row=0, column=3, padx=5, pady=3)

            tk.Label(frame_grid, text="Stock Inicial:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
            self.entry_nuevo_stock = tk.Entry(frame_grid, font=("Arial", 10), width=10,
                                              validate="key", validatecommand=vcmd_int2)
            self.entry_nuevo_stock.grid(row=1, column=1, padx=5, pady=3, sticky="w")

            tk.Label(frame_grid, text="Stock Mínimo:").grid(row=1, column=2, sticky="w", padx=5, pady=3)
            self.entry_nuevo_min = tk.Entry(frame_grid, font=("Arial", 10), width=10,
                                            validate="key", validatecommand=vcmd_int2)
            self.entry_nuevo_min.grid(row=1, column=3, padx=5, pady=3, sticky="w")

            tk.Label(frame_grid, text="Precio Venta:").grid(row=2, column=0, sticky="w", padx=5, pady=3)
            self.entry_nuevo_pventa = tk.Entry(frame_grid, font=("Arial", 10), width=10,
                                               validate="key", validatecommand=vcmd_dec2)
            self.entry_nuevo_pventa.grid(row=2, column=1, padx=5, pady=3, sticky="w")

            tk.Label(frame_grid, text="Precio Costo:").grid(row=2, column=2, sticky="w", padx=5, pady=3)
            self.entry_nuevo_pcosto = tk.Entry(frame_grid, font=("Arial", 10), width=10,
                                               validate="key", validatecommand=vcmd_dec2)
            self.entry_nuevo_pcosto.grid(row=2, column=3, padx=5, pady=3, sticky="w")

            tk.Label(frame_grid, text="Categoría:").grid(row=3, column=0, sticky="w", padx=5, pady=3)
            self.combo_nuevo_cat = ttk.Combobox(frame_grid, values=self.CATEGORIAS,
                                                 state="readonly", width=22)
            self.combo_nuevo_cat.grid(row=3, column=1, padx=5, pady=3, sticky="w")
            self.combo_nuevo_cat.current(0)

            tk.Label(frame_grid, text="Proveedor:").grid(row=4, column=0, sticky="w", padx=5, pady=3)
            self.combo_nuevo_prov = ttk.Combobox(frame_grid, state="readonly", width=22)
            self.combo_nuevo_prov.grid(row=4, column=1, padx=5, pady=3, sticky="w")
            self._refrescar_combo_proveedor(self.combo_nuevo_prov)

            tk.Button(frame_grid, text="✅ Registrar Producto",
                      command=self.añadir_nuevo_producto,
                      bg="#4CAF50", fg="white",
                      font=("Arial", 10, "bold")).grid(row=4, column=2, columnspan=2, padx=10, pady=10)

    # ──────────────────────────────────────────────────────────────────────
    # PESTAÑA INVENTARIO GENERAL
    # ──────────────────────────────────────────────────────────────────────
    def setup_tab_stock(self):
        frame_filtros = tk.Frame(self.tab_stock)
        frame_filtros.pack(pady=5, fill="x", padx=10)

        tk.Label(frame_filtros, text="Filtrar por categoría:").pack(side="left", padx=5)
        self.combo_filtro_cat = ttk.Combobox(frame_filtros, values=["Todas"] + self.CATEGORIAS,
                                              state="readonly", width=20)
        self.combo_filtro_cat.pack(side="left", padx=5)
        self.combo_filtro_cat.current(0)
        self.combo_filtro_cat.bind("<<ComboboxSelected>>", lambda e: self.actualizar_vistas())

        tk.Button(frame_filtros, text="🔄 Actualizar", command=self.actualizar_vistas,
                  bg="#2196F3", fg="white").pack(side="left", padx=10)

        es_empleado = self.rol_actual == "empleado"
        if es_empleado:
            columnas = ("Código", "Producto", "Categoría", "Stock", "Mínimo", "Estado")
        else:
            columnas = ("Código", "Producto", "Categoría", "Stock", "Mínimo",
                        "P.Venta", "P.Costo", "Margen", "Valor", "Estado")

        self.tree_stock = ttk.Treeview(self.tab_stock, columns=columnas, show="headings", height=20)

        for col in ("Código", "Producto", "Categoría", "Stock", "Mínimo", "Estado"):
            self.tree_stock.heading(col, text=col)
        for col, w in [("Código", 110), ("Producto", 220), ("Categoría", 130),
                       ("Stock", 90), ("Mínimo", 90), ("Estado", 130)]:
            self.tree_stock.column(col, width=w)

        if not es_empleado:
            for col, label in [("P.Venta", "P. Venta"), ("P.Costo", "P. Costo"),
                                ("Margen", "Margen %"), ("Valor", "Valor Inv.")]:
                self.tree_stock.heading(col, text=label)
                self.tree_stock.column(col, width=85)

        scrollbar = ttk.Scrollbar(self.tab_stock, orient="vertical", command=self.tree_stock.yview)
        self.tree_stock.configure(yscrollcommand=scrollbar.set)
        self.tree_stock.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=10)

    # ──────────────────────────────────────────────────────────────────────
    # PESTAÑA ALERTAS DE STOCK
    # ──────────────────────────────────────────────────────────────────────
    def setup_tab_bajo_stock(self):
        tk.Label(self.tab_bajo_stock, text="⚠️ Productos que necesitan reabastecimiento",
                 font=("Arial", 12, "bold"), fg="#d32f2f").pack(pady=10)

        self.tree_bajo = ttk.Treeview(
            self.tab_bajo_stock,
            columns=("Código", "Producto", "Categoría", "Stock", "Mínimo",
                     "Faltante", "Proveedor", "Contacto", "Teléfono"),
            show="headings", height=15)
        for col, label, w in [
            ("Código",    "Código",             90),
            ("Producto",  "Producto",           180),
            ("Categoría", "Categoría",          110),
            ("Stock",     "Stock Actual",        80),
            ("Mínimo",    "Stock Mínimo",        80),
            ("Faltante",  "Cant. Sugerida",      90),
            ("Proveedor", "Proveedor",           140),
            ("Contacto",  "Contacto",            130),
            ("Teléfono",  "Teléfono",            110),
        ]:
            self.tree_bajo.heading(col, text=label)
            self.tree_bajo.column(col, width=w)

        scrollbar_h = ttk.Scrollbar(self.tab_bajo_stock, orient="horizontal",
                                     command=self.tree_bajo.xview)
        scrollbar   = ttk.Scrollbar(self.tab_bajo_stock, orient="vertical",
                                     command=self.tree_bajo.yview)
        self.tree_bajo.configure(yscrollcommand=scrollbar.set,
                                  xscrollcommand=scrollbar_h.set)
        self.tree_bajo.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(10, 0))
        scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=(10, 0))
        scrollbar_h.pack(side="bottom", fill="x", padx=(10, 10), pady=(0, 5))


    # ──────────────────────────────────────────────────────────────────────
    # PESTAÑA PROVEEDORES — CRUD completo
    # ──────────────────────────────────────────────────────────────────────
    def setup_tab_proveedores(self):
        """Pestaña exclusiva para administrador y gerente."""
        # ── Panel superior: formulario alta/edición ───────────────────────
        frame_form = tk.LabelFrame(self.tab_proveedores,
                                   text="➕ Registrar / Editar Proveedor",
                                   padx=15, pady=10)
        frame_form.pack(pady=8, padx=15, fill="x")

        labels = ["ID / RFC", "Empresa", "Contacto", "Teléfono", "Correo", "Días entrega"]
        self._prov_entries = {}
        for i, lbl in enumerate(labels):
            r, c = divmod(i, 2)
            tk.Label(frame_form, text=f"{lbl}:", width=12, anchor="w").grid(
                row=r, column=c * 2, sticky="w", padx=5, pady=3)
            ent = tk.Entry(frame_form, font=("Arial", 10), width=25)
            ent.grid(row=r, column=c * 2 + 1, padx=5, pady=3, sticky="w")
            self._prov_entries[lbl] = ent

        frame_btns = tk.Frame(frame_form)
        frame_btns.grid(row=3, column=0, columnspan=4, pady=8)

        tk.Button(frame_btns, text="💾 Guardar Proveedor",
                  command=self._guardar_proveedor,
                  bg="#4CAF50", fg="white", width=18).pack(side="left", padx=5)
        tk.Button(frame_btns, text="🔄 Limpiar Formulario",
                  command=self._limpiar_form_proveedor,
                  bg="#FF9800", fg="white", width=18).pack(side="left", padx=5)
        self.btn_inactivar_prov = tk.Button(
            frame_btns, text="🚫 Desactivar Proveedor",
            command=self._desactivar_proveedor,
            bg="#B71C1C", fg="white", width=20, state="disabled")
        self.btn_inactivar_prov.pack(side="left", padx=5)
        self.btn_eliminar_prov = tk.Button(
            frame_btns, text="🗑️ Eliminar Definitivamente",
            command=self._eliminar_proveedor_definitivo,
            bg="#4A148C", fg="white", width=22, state="disabled")
        self.btn_eliminar_prov.pack(side="left", padx=5)

        # Variable para saber si estamos editando
        self._prov_id_editando = None

        # ── Panel inferior: tabla de proveedores ──────────────────────────
        frame_tabla = tk.LabelFrame(self.tab_proveedores,
                                    text="📋 Proveedores Registrados",
                                    padx=10, pady=8)
        frame_tabla.pack(pady=5, padx=15, fill="both", expand=True)

        # Filtro activos/todos
        frame_filtro = tk.Frame(frame_tabla)
        frame_filtro.pack(fill="x", pady=(0, 5))
        tk.Label(frame_filtro, text="Mostrar:").pack(side="left", padx=5)
        self.combo_filtro_prov = ttk.Combobox(
            frame_filtro, values=["Solo Activos", "Todos"],
            state="readonly", width=14)
        self.combo_filtro_prov.current(0)
        self.combo_filtro_prov.pack(side="left", padx=5)
        self.combo_filtro_prov.bind("<<ComboboxSelected>>",
                                    lambda e: self.actualizar_tabla_proveedores())
        tk.Button(frame_filtro, text="🔄 Actualizar",
                  command=self.actualizar_tabla_proveedores,
                  bg="#2196F3", fg="white").pack(side="left", padx=8)

        cols_prov = ("RFC", "Empresa", "Contacto", "Teléfono",
                     "Correo", "Días", "Estado", "Productos")
        self.tree_prov = ttk.Treeview(frame_tabla, columns=cols_prov,
                                       show="headings", height=12)
        for col, label, w in [
            ("RFC",      "ID / RFC",         110),
            ("Empresa",  "Empresa",          160),
            ("Contacto", "Contacto",         130),
            ("Teléfono", "Teléfono",         100),
            ("Correo",   "Correo",           170),
            ("Días",     "Días entrega",      80),
            ("Estado",   "Estado",            80),
            ("Productos","# Productos",       80),
        ]:
            self.tree_prov.heading(col, text=label)
            self.tree_prov.column(col, width=w)

        sb_v = ttk.Scrollbar(frame_tabla, orient="vertical",
                              command=self.tree_prov.yview)
        sb_h = ttk.Scrollbar(frame_tabla, orient="horizontal",
                              command=self.tree_prov.xview)
        self.tree_prov.configure(yscrollcommand=sb_v.set,
                                  xscrollcommand=sb_h.set)
        self.tree_prov.pack(side="left", fill="both", expand=True)
        sb_v.pack(side="right", fill="y")
        sb_h.pack(side="bottom", fill="x")

        self.tree_prov.bind("<<TreeviewSelect>>", self._al_seleccionar_proveedor)

        # Colores: inactivos en gris
        self.tree_prov.tag_configure("inactivo", foreground="#9E9E9E")
        self.tree_prov.tag_configure("activo",   foreground="#1B5E20")

        self.actualizar_tabla_proveedores()

    # ── Helpers de proveedores ─────────────────────────────────────────────
    def _generar_id_proveedor(self):
        """Genera un ID único de 8 caracteres en base al timestamp."""
        import time
        return f"PROV{int(time.time() * 1000) % 100000000:08d}"

    def _refrescar_combo_proveedor(self, combo):
        """Rellena un Combobox con los proveedores activos (nombre – RFC)."""
        activos = [
            f"{d['empresa']} ({pid})"
            for pid, d in sorted(self.proveedores.items(),
                                  key=lambda x: x[1].get('empresa', ''))
            if d.get('activo', True)
        ]
        combo['values'] = activos if activos else ["— Sin proveedores activos —"]
        if activos:
            combo.current(0)
        else:
            combo.set("— Sin proveedores activos —")

    def _id_desde_combo_prov(self, valor):
        """Extrae el RFC/ID del texto 'Empresa (RFC)'."""
        if "(" in valor and valor.endswith(")"):
            return valor.rsplit("(", 1)[1][:-1].strip()
        return None

    def _nombre_proveedor(self, prov_id):
        """Devuelve el nombre de empresa del proveedor, o cadena vacía."""
        if not prov_id or prov_id not in self.proveedores:
            return ""
        return self.proveedores[prov_id].get("empresa", "")

    def _limpiar_form_proveedor(self):
        for ent in self._prov_entries.values():
            ent.config(state="normal")
            ent.delete(0, tk.END)
        self._prov_id_editando = None
        self.btn_inactivar_prov.config(state="disabled")
        self.btn_eliminar_prov.config(state="disabled")
        self._prov_entries["ID / RFC"].focus()

    def _al_seleccionar_proveedor(self, event):
        """Carga el proveedor seleccionado en el formulario de edición."""
        sel = self.tree_prov.selection()
        if not sel:
            return
        rfc = self.tree_prov.item(sel[0])["values"][0]
        if rfc not in self.proveedores:
            return

        d = self.proveedores[rfc]
        self._prov_id_editando = rfc

        mapping = {
            "ID / RFC":     rfc,
            "Empresa":      d.get("empresa", ""),
            "Contacto":     d.get("contacto", ""),
            "Teléfono":     d.get("telefono", ""),
            "Correo":       d.get("correo", ""),
            "Días entrega": d.get("dias_entrega", ""),
        }
        for lbl, val in mapping.items():
            ent = self._prov_entries[lbl]
            ent.config(state="normal")
            ent.delete(0, tk.END)
            ent.insert(0, str(val))

        # El RFC no se puede cambiar una vez registrado
        self._prov_entries["ID / RFC"].config(state="disabled")

        activo = d.get("activo", True)
        if activo:
            self.btn_inactivar_prov.config(
                state="normal", text="🚫 Desactivar Proveedor", bg="#B71C1C")
        else:
            self.btn_inactivar_prov.config(
                state="normal", text="✅ Reactivar Proveedor", bg="#388E3C")

        # Botón eliminar definitivamente: solo si 0 productos vinculados
        vinculados = sum(1 for p in self.productos.values() if p.get("proveedor_id") == rfc)
        if vinculados == 0:
            self.btn_eliminar_prov.config(state="normal")
        else:
            self.btn_eliminar_prov.config(state="disabled")

    def _guardar_proveedor(self):
        """Alta de nuevo proveedor o actualización de uno existente."""
        rfc      = self._prov_entries["ID / RFC"].get().strip().upper()
        empresa  = self._prov_entries["Empresa"].get().strip()
        contacto = self._prov_entries["Contacto"].get().strip()
        telefono = self._prov_entries["Teléfono"].get().strip()
        correo   = self._prov_entries["Correo"].get().strip()
        dias     = self._prov_entries["Días entrega"].get().strip()

        if not rfc or not empresa:
            messagebox.showerror("Error", "El ID/RFC y el nombre de empresa son obligatorios.")
            return

        # ── Validaciones de formato ──────────────────────────────────────
        if not self._prov_id_editando:
            # RFC mexicano: 3-4 letras + 6 dígitos fecha + 3 homoclave (letras/dígitos)
            if not re.match(r'^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$', rfc):
                messagebox.showerror(
                    "RFC inválido",
                    "El RFC debe tener el formato:\n"
                    "  • 3-4 letras + 6 dígitos (fecha) + 3 caracteres (homoclave)\n"
                    "  Ejemplo: XAXX010101000"
                )
                return

        if telefono and not re.match(r'^\d{10}$', telefono):
            messagebox.showerror(
                "Teléfono inválido",
                "El teléfono debe contener exactamente 10 dígitos.\n"
                "Ejemplo: 5512345678"
            )
            return

        if correo and not re.match(r'^[\w\.\+\-]+@[\w\-]+\.[a-zA-Z]{2,}$', correo):
            messagebox.showerror(
                "Correo inválido",
                "Ingrese un correo electrónico válido.\n"
                "Ejemplo: proveedor@empresa.com"
            )
            return

        if self._prov_id_editando:
            # Actualizacion: pedir motivo y mostrar impacto
            pid = self._prov_id_editando
            datos_ant = self.proveedores[pid]

            cambios_detectados = []
            if datos_ant.get("empresa", "")      != empresa:  cambios_detectados.append(f"Nombre: '{datos_ant.get('empresa','')}' -> '{empresa}'")
            if datos_ant.get("contacto", "")     != contacto: cambios_detectados.append(f"Contacto: '{datos_ant.get('contacto','')}' -> '{contacto}'")
            if datos_ant.get("telefono", "")     != telefono: cambios_detectados.append(f"Telefono: '{datos_ant.get('telefono','')}' -> '{telefono}'")
            if datos_ant.get("correo", "")       != correo:   cambios_detectados.append(f"Correo: '{datos_ant.get('correo','')}' -> '{correo}'")
            if datos_ant.get("dias_entrega", "") != dias:     cambios_detectados.append(f"Dias entrega: '{datos_ant.get('dias_entrega','')}' -> '{dias}'")

            if not cambios_detectados:
                messagebox.showinfo("Sin cambios", "No se detectaron cambios en los datos del proveedor.")
                return

            prods_vinculados = [n for n, p in self.productos.items() if p.get("proveedor_id") == pid]

            dlg = tk.Toplevel(self.root)
            dlg.title("Confirmar Modificacion de Proveedor")
            dlg.geometry("540x490")
            dlg.resizable(False, False)
            dlg.grab_set()

            tk.Label(dlg, text="⚠️  Modificacion de Proveedor",
                     font=("Arial", 13, "bold"), fg="#E65100").pack(pady=(14, 4))

            frame_cambios = tk.LabelFrame(dlg, text="📝  Cambios detectados", padx=10, pady=8)
            frame_cambios.pack(fill="x", padx=15, pady=(4, 6))
            for c in cambios_detectados:
                tk.Label(frame_cambios, text=f"  • {c}", font=("Arial", 9),
                         fg="#1565C0", anchor="w", justify="left").pack(fill="x")

            frame_impacto = tk.LabelFrame(dlg, text="🗄️  Impacto en la Base de Datos", padx=10, pady=8)
            frame_impacto.pack(fill="x", padx=15, pady=(0, 6))
            if prods_vinculados:
                lista_prods = ', '.join(prods_vinculados[:5]) + ('...' if len(prods_vinculados) > 5 else '')
                n_prods = len(prods_vinculados)
                txt_impacto = ("ATENCION: Este proveedor esta asignado a " + str(n_prods) + " producto(s):\n"
                               "  " + lista_prods + "\n\n"
                               "Los datos actualizados (nombre, contacto, etc.) quedaran\n"
                               "reflejados en todos esos registros vinculados.")
                color_impacto = "#B71C1C"
            else:
                txt_impacto = "Este proveedor no tiene productos asignados.\nLa modificacion no afectara ningun producto del inventario."
                color_impacto = "#2E7D32"
            tk.Label(frame_impacto, text=txt_impacto, font=("Arial", 9),
                     fg=color_impacto, justify="left").pack(anchor="w")

            frame_motivo = tk.LabelFrame(dlg, text="📋  Motivo de la modificacion  * (obligatorio)", padx=10, pady=8)
            frame_motivo.pack(fill="x", padx=15, pady=(0, 6))
            tk.Label(frame_motivo, text="Ej: 'Cambio de representante', 'Actualizacion de datos de contacto'",
                     font=("Arial", 8), fg="#757575").pack(anchor="w")
            entry_motivo_mod = tk.Entry(frame_motivo, font=("Arial", 10), width=55, bg="#fff8e1")
            entry_motivo_mod.pack(fill="x", pady=(4, 0))
            entry_motivo_mod.focus()

            resultado = {"ok": False}

            def confirmar_mod():
                motivo = entry_motivo_mod.get().strip()
                if not motivo:
                    messagebox.showerror("Motivo obligatorio",
                                         "Debe ingresar un motivo para registrar la modificacion.", parent=dlg)
                    entry_motivo_mod.focus()
                    return
                resultado["ok"] = True
                resultado["motivo"] = motivo
                dlg.destroy()

            def cancelar_mod():
                dlg.destroy()

            frame_btns_mod = tk.Frame(dlg)
            frame_btns_mod.pack(pady=10)
            tk.Button(frame_btns_mod, text="✅  Confirmar Modificacion", command=confirmar_mod,
                      bg="#1565C0", fg="white", font=("Arial", 10, "bold"), width=22).pack(side="left", padx=5)
            tk.Button(frame_btns_mod, text="❌  Cancelar", command=cancelar_mod,
                      bg="#757575", fg="white", font=("Arial", 10, "bold"), width=14).pack(side="left", padx=5)

            dlg.wait_window()
            if not resultado.get("ok"):
                return

            motivo_mod = resultado["motivo"]
            cambios_log = cambios_detectados

        else:
            # Alta nueva
            pid = rfc
            motivo_mod = "Alta de nuevo proveedor"
            cambios_log = []
            if pid in self.proveedores:
                messagebox.showerror("Error",
                                     f"Ya existe un proveedor con el ID/RFC '{pid}'.")
                return

        nuevo = {
            "empresa":      empresa,
            "contacto":     contacto,
            "telefono":     telefono,
            "correo":       correo,
            "dias_entrega": dias,
            "activo":       self.proveedores.get(pid, {}).get("activo", True)
        }
        self.proveedores[pid] = nuevo

        accion = "actualizado" if self._prov_id_editando else "registrado"
        detalle_hist = f"PROVEEDOR {accion.upper()}: {empresa} (RFC: {pid}) | Motivo: {motivo_mod}"
        if cambios_log:
            detalle_hist += f" | Cambios: {'; '.join(cambios_log)}"
        self.registrar_historial(detalle_hist)

        def post_guardado():
            self.actualizar_tabla_proveedores()
            if self.tiene_permiso("crear"):
                self._refrescar_combo_proveedor(self.combo_nuevo_prov)
            messagebox.showinfo("Exito", f"Proveedor '{empresa}' {accion} correctamente.")
            self._limpiar_form_proveedor()

        self.guardar_datos(callback=post_guardado)

    def _desactivar_proveedor(self):
        """
        Borrado logico: cambia activo=False/True.
        Solicita motivo obligatorio y muestra el impacto sobre productos vinculados.
        """
        if not self._prov_id_editando:
            return

        pid    = self._prov_id_editando
        d      = self.proveedores[pid]
        activo = d.get("activo", True)

        vinculados = [n for n, p in self.productos.items() if p.get("proveedor_id") == pid]
        accion_texto = "DESACTIVAR" if activo else "REACTIVAR"

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Confirmar {accion_texto.capitalize()} Proveedor")
        dlg.geometry("540x430")
        dlg.resizable(False, False)
        dlg.grab_set()

        color_titulo = "#B71C1C" if activo else "#2E7D32"
        icono = "🚫" if activo else "✅"
        tk.Label(dlg, text=f"{icono}  {accion_texto} Proveedor",
                 font=("Arial", 13, "bold"), fg=color_titulo).pack(pady=(14, 2))
        tk.Label(dlg, text=f"{d['empresa']}  (RFC: {pid})",
                 font=("Arial", 10), fg="#424242").pack(pady=(0, 8))

        # Impacto en base de datos
        frame_impacto = tk.LabelFrame(dlg, text="🗄️  Impacto en la Base de Datos", padx=12, pady=10)
        frame_impacto.pack(fill="x", padx=15, pady=(0, 8))

        if activo:
            if vinculados:
                lista_prods = ', '.join(vinculados[:5]) + ('...' if len(vinculados) > 5 else '')
                n_v = len(vinculados)
                impacto_txt = ("ATENCION: " + str(n_v) + " producto(s) tienen este proveedor asignado:\n"
                               "    " + lista_prods + "\n\n"
                               "Al DESACTIVARLO:\n"
                               "  - Los productos existentes conservaran la referencia al proveedor.\n"
                               "  - Este proveedor NO estara disponible para asignar a nuevos productos.\n"
                               "  - Las alertas de stock bajo seguiran mostrando sus datos de contacto.")
                color_imp = "#B71C1C"
            else:
                impacto_txt = ("Este proveedor no tiene productos asignados.\n"
                               "Desactivarlo no afectara ningun producto del inventario.\n"
                               "Simplemente dejara de aparecer en los menus de seleccion.")
                color_imp = "#2E7D32"
        else:
            if vinculados:
                n_v = len(vinculados)
                impacto_txt = ("Al REACTIVAR este proveedor:\n"
                               "  - Volvera a estar disponible para asignar a nuevos productos.\n"
                               "  - Los " + str(n_v) + " producto(s) vinculados seguiran sin cambios.")
            else:
                impacto_txt = ("Al REACTIVAR este proveedor:\n"
                               "    Volvera a aparecer en los menus de seleccion de proveedor.")
            color_imp = "#1565C0"

        tk.Label(frame_impacto, text=impacto_txt, font=("Arial", 9),
                 fg=color_imp, justify="left").pack(anchor="w")

        # Motivo obligatorio
        frame_motivo = tk.LabelFrame(dlg, text=f"📋  Motivo para {accion_texto.lower()}  * (obligatorio)", padx=10, pady=8)
        frame_motivo.pack(fill="x", padx=15, pady=(0, 8))
        ej = "'Proveedor inactivo temporalmente'" if activo else "'Proveedor retoma actividad'"
        tk.Label(frame_motivo, text=f"Ej: {ej}",
                 font=("Arial", 8), fg="#757575").pack(anchor="w")
        entry_motivo_des = tk.Entry(frame_motivo, font=("Arial", 10), width=55, bg="#fff8e1")
        entry_motivo_des.pack(fill="x", pady=(4, 0))
        entry_motivo_des.focus()

        resultado = {"ok": False}

        def confirmar_des():
            motivo = entry_motivo_des.get().strip()
            if not motivo:
                messagebox.showerror("Motivo obligatorio",
                                     "Debe ingresar un motivo para continuar.", parent=dlg)
                entry_motivo_des.focus()
                return
            resultado["ok"] = True
            resultado["motivo"] = motivo
            dlg.destroy()

        def cancelar_des():
            dlg.destroy()

        color_btn = "#B71C1C" if activo else "#388E3C"
        frame_btns_des = tk.Frame(dlg)
        frame_btns_des.pack(pady=10)
        tk.Button(frame_btns_des, text=f"{icono}  Confirmar {accion_texto.capitalize()}", command=confirmar_des,
                  bg=color_btn, fg="white", font=("Arial", 10, "bold"), width=24).pack(side="left", padx=5)
        tk.Button(frame_btns_des, text="❌  Cancelar", command=cancelar_des,
                  bg="#757575", fg="white", font=("Arial", 10, "bold"), width=14).pack(side="left", padx=5)

        dlg.wait_window()
        if not resultado.get("ok"):
            return

        motivo_des = resultado["motivo"]

        if activo:
            self.proveedores[pid]["activo"] = False
            accion = "DESACTIVADO"
        else:
            self.proveedores[pid]["activo"] = True
            accion = "REACTIVADO"

        self.registrar_historial(
            f"PROVEEDOR {accion}: {d['empresa']} (RFC: {pid}) | Motivo: {motivo_des}"
            + (f" | Productos afectados: {len(vinculados)}" if vinculados else "")
        )

        def post_guardado():
            self.actualizar_tabla_proveedores()
            if self.tiene_permiso("crear"):
                self._refrescar_combo_proveedor(self.combo_nuevo_prov)
            messagebox.showinfo("Operacion Completada",
                                f"Proveedor '{d['empresa']}' {accion.lower()} correctamente.")
            self._limpiar_form_proveedor()

        self.guardar_datos(callback=post_guardado)

    def _eliminar_proveedor_definitivo(self):
        """
        Borrado fisico del proveedor. Solo permitido si tiene 0 productos vinculados.
        Solicita motivo obligatorio y muestra advertencia de impacto permanente.
        """
        if not self._prov_id_editando:
            return

        pid = self._prov_id_editando
        d   = self.proveedores[pid]

        # Verificacion de seguridad: no se puede eliminar si tiene productos
        vinculados = [n for n, p in self.productos.items() if p.get("proveedor_id") == pid]
        if vinculados:
            lista_prods = chr(10).join(f"  • {n}" for n in vinculados[:8])
            if len(vinculados) > 8:
                lista_prods += f"\n  ... y {len(vinculados)-8} mas"
            messagebox.showerror(
                "Eliminacion no permitida",
                f"No es posible eliminar al proveedor '{d['empresa']}' porque\n"
                f"tiene {len(vinculados)} producto(s) asignado(s):\n\n"
                f"{lista_prods}\n\n"
                "Opciones disponibles:\n"
                "  • Reasigne esos productos a otro proveedor primero.\n"
                "  • O use el boton 'Desactivar' para ocultarlo sin eliminarlo."
            )
            return

        # Dialogo de confirmacion con motivo obligatorio
        dlg = tk.Toplevel(self.root)
        dlg.title("ELIMINAR Proveedor Definitivamente")
        dlg.geometry("540x460")
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text="🗑️  ELIMINAR PROVEEDOR DEFINITIVAMENTE",
                 font=("Arial", 13, "bold"), fg="#B71C1C").pack(pady=(14, 2))
        tk.Label(dlg, text=f"{d['empresa']}  (RFC: {pid})",
                 font=("Arial", 10), fg="#424242").pack(pady=(0, 8))

        # Advertencia de impacto irreversible
        frame_advertencia = tk.LabelFrame(dlg, text="⛔  Advertencia — Impacto en la Base de Datos", padx=12, pady=10)
        frame_advertencia.pack(fill="x", padx=15, pady=(0, 8))
        advertencia_txt = (
            "Esta accion es PERMANENTE e IRREVERSIBLE.\n\n"
            "Al eliminar este proveedor:\n"
            "  - Se borrara COMPLETAMENTE de Firebase y del sistema.\n"
            "  - No podra recuperarse; habra que volver a darlo de alta.\n"
            "  - Su historial de movimientos se conservara en el log\n"
            "    como referencia, pero el proveedor dejara de existir.\n\n"
            "Actualmente no tiene productos asignados, por lo que\n"
            "ningun producto del inventario se vera afectado."
        )
        tk.Label(frame_advertencia, text=advertencia_txt, font=("Arial", 9),
                 fg="#B71C1C", justify="left").pack(anchor="w")

        # Motivo obligatorio
        frame_motivo = tk.LabelFrame(dlg, text="📋  Motivo de la eliminacion  * (obligatorio)", padx=10, pady=8)
        frame_motivo.pack(fill="x", padx=15, pady=(0, 8))
        tk.Label(frame_motivo, text="Ej: 'Proveedor cerro operaciones', 'Duplicado creado por error'",
                 font=("Arial", 8), fg="#757575").pack(anchor="w")
        entry_motivo_eli = tk.Entry(frame_motivo, font=("Arial", 10), width=55, bg="#fff8e1")
        entry_motivo_eli.pack(fill="x", pady=(4, 0))
        entry_motivo_eli.focus()

        resultado = {"ok": False}

        def confirmar_eli():
            motivo = entry_motivo_eli.get().strip()
            if not motivo:
                messagebox.showerror("Motivo obligatorio",
                                     "Debe ingresar un motivo para poder eliminar el proveedor.", parent=dlg)
                entry_motivo_eli.focus()
                return
            # Segunda confirmacion de seguridad
            if not messagebox.askyesno(
                "Confirmacion Final",
                f"¿Esta COMPLETAMENTE seguro de eliminar permanentemente\n"
                f"al proveedor '{d['empresa']}'?\n\n"
                "Esta accion NO se puede deshacer.",
                parent=dlg
            ):
                return
            resultado["ok"] = True
            resultado["motivo"] = motivo
            dlg.destroy()

        def cancelar_eli():
            dlg.destroy()

        frame_btns_eli = tk.Frame(dlg)
        frame_btns_eli.pack(pady=10)
        tk.Button(frame_btns_eli, text="🗑️  ELIMINAR DEFINITIVAMENTE", command=confirmar_eli,
                  bg="#B71C1C", fg="white", font=("Arial", 10, "bold"), width=24).pack(side="left", padx=5)
        tk.Button(frame_btns_eli, text="❌  Cancelar", command=cancelar_eli,
                  bg="#757575", fg="white", font=("Arial", 10, "bold"), width=14).pack(side="left", padx=5)

        dlg.wait_window()
        if not resultado.get("ok"):
            return

        motivo_eli = resultado["motivo"]
        nombre_empresa = d["empresa"]

        del self.proveedores[pid]
        self.registrar_historial(
            f"PROVEEDOR ELIMINADO DEFINITIVAMENTE: {nombre_empresa} (RFC: {pid}) | Motivo: {motivo_eli}"
        )

        def post_guardado():
            self.actualizar_tabla_proveedores()
            if self.tiene_permiso("crear"):
                self._refrescar_combo_proveedor(self.combo_nuevo_prov)
            messagebox.showinfo("Eliminado", f"Proveedor '{nombre_empresa}' eliminado permanentemente.")
            self._limpiar_form_proveedor()

        self.guardar_datos(callback=post_guardado)

    def actualizar_tabla_proveedores(self):
        """Refresca el Treeview de proveedores según el filtro activo/todos."""
        for item in self.tree_prov.get_children():
            self.tree_prov.delete(item)

        solo_activos = self.combo_filtro_prov.get() == "Solo Activos"

        # Contar productos por proveedor
        conteo = {}
        for p in self.productos.values():
            pid = p.get("proveedor_id")
            if pid:
                conteo[pid] = conteo.get(pid, 0) + 1

        for pid, d in sorted(self.proveedores.items(),
                              key=lambda x: x[1].get("empresa", "")):
            activo = d.get("activo", True)
            if solo_activos and not activo:
                continue
            estado = "✅ Activo" if activo else "🚫 Inactivo"
            tag    = "activo" if activo else "inactivo"
            self.tree_prov.insert("", tk.END, tags=(tag,), values=(
                pid,
                d.get("empresa", ""),
                d.get("contacto", ""),
                d.get("telefono", ""),
                d.get("correo", ""),
                d.get("dias_entrega", ""),
                estado,
                conteo.get(pid, 0),
            ))

    # ──────────────────────────────────────────────────────────────────────
    # PESTAÑA REPORTES
    # ──────────────────────────────────────────────────────────────────────
    def setup_tab_reportes(self):
        self.frame_resumen = tk.LabelFrame(self.tab_reportes, text="📊 Resumen General", padx=20, pady=10)
        self.frame_resumen.pack(pady=10, padx=20, fill="both", expand=True)

        self.lbl_total_productos    = tk.Label(self.frame_resumen, text="", font=("Arial", 12))
        self.lbl_total_productos.pack(pady=5)
        self.lbl_valor_inventario   = tk.Label(self.frame_resumen, text="", font=("Arial", 12))
        self.lbl_valor_inventario.pack(pady=5)
        self.lbl_productos_criticos = tk.Label(self.frame_resumen, text="", font=("Arial", 12))
        self.lbl_productos_criticos.pack(pady=5)
        self.lbl_margen_promedio    = tk.Label(self.frame_resumen, text="", font=("Arial", 12))
        self.lbl_margen_promedio.pack(pady=5)

        self.frame_cat_stats = tk.LabelFrame(self.tab_reportes, text="📈 Por Categoría", padx=20, pady=10)
        self.frame_cat_stats.pack(pady=10, padx=20, fill="both", expand=True)

        self.tree_cat_stats = ttk.Treeview(self.frame_cat_stats,
                                            columns=("Categoría", "Productos", "Stock Total", "Valor"),
                                            show="headings", height=10)
        for col, label in [("Categoría", "Categoría"), ("Productos", "# Productos"),
                            ("Stock Total", "Stock Total"), ("Valor", "Valor Total")]:
            self.tree_cat_stats.heading(col, text=label)
        self.tree_cat_stats.pack(fill="both", expand=True)

        tk.Button(self.tab_reportes, text="🔄 Actualizar Reportes", command=self.actualizar_reportes,
                  bg="#4CAF50", fg="white", font=("Arial", 10, "bold")).pack(pady=10)

    # ──────────────────────────────────────────────────────────────────────
    # PESTAÑA HISTORIAL
    # ──────────────────────────────────────────────────────────────────────
    def setup_tab_historial(self):
        frame_filtro_hist = tk.Frame(self.tab_historial)
        frame_filtro_hist.pack(pady=5, fill="x", padx=10)

        tk.Label(frame_filtro_hist, text="Mostrar últimas:").pack(side="left", padx=5)
        self.spin_historial_limit = tk.Spinbox(frame_filtro_hist, from_=10, to=500, increment=10, width=10)
        self.spin_historial_limit.delete(0, tk.END)
        self.spin_historial_limit.insert(0, "100")
        self.spin_historial_limit.pack(side="left", padx=5)

        tk.Button(frame_filtro_hist, text="🔄 Actualizar", command=self.actualizar_historial,
                  bg="#2196F3", fg="white").pack(side="left", padx=10)

        self.txt_historial = tk.Text(self.tab_historial, state="disabled",
                                      bg="#f5f5f5", font=("Consolas", 9), wrap="word")
        scrollbar_hist = ttk.Scrollbar(self.tab_historial, orient="vertical",
                                        command=self.txt_historial.yview)
        self.txt_historial.configure(yscrollcommand=scrollbar_hist.set)
        self.txt_historial.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scrollbar_hist.pack(side="right", fill="y", padx=(0, 10), pady=10)

    # ──────────────────────────────────────────────────────────────────────
    # BÚSQUEDA Y AUTOCOMPLETADO — lógica corregida
    # ──────────────────────────────────────────────────────────────────────
    def controlar_autocompletado(self, event):
        """
        Dispara al escribir en el campo de búsqueda.
        SOLO muestra sugerencias. Nunca carga un producto automáticamente,
        aunque solo haya una coincidencia.
        """
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return

        texto = self.entry_busqueda.get().strip().lower()
        if not texto:
            self.ocultar_lista()
            return

        self._calcular_y_mostrar_sugerencias(texto)

    def _actualizar_sugerencias_por_filtro(self):
        """
        Se llama cuando cambia Categoría o Estado Stock.
        Si hay texto en la caja de búsqueda, refresca las sugerencias.
        Si no hay texto, limpia el panel de info (no carga ningún producto).
        """
        texto = self.entry_busqueda.get().strip().lower()
        if texto:
            self._calcular_y_mostrar_sugerencias(texto)
        else:
            self.ocultar_lista()
            # No cargar ningún producto automáticamente al cambiar filtros sin texto

    def _calcular_y_mostrar_sugerencias(self, texto):
        """Calcula coincidencias respetando solo el filtro de categoría (no precio ni estado)."""
        categoria_filtro = self.combo_filtro_categ.get()

        coincidencias = []
        for nombre, datos in self.productos.items():
            if not (texto in nombre.lower() or
                    texto in datos.get("codigo_barras", "").lower()):
                continue
            if (categoria_filtro != "Todas" and
                    datos.get("categoria", "") != categoria_filtro):
                continue
            coincidencias.append(nombre)

        coincidencias.sort()

        if coincidencias:
            self.mostrar_sugerencias(coincidencias)
        else:
            self.ocultar_lista()

    def mostrar_sugerencias(self, sugerencias):
        """
        Muestra TODOS los productos coincidentes con scrollbar.
        Altura dinámica: máximo 8 filas visibles. Sin límite artificial de ítems.
        """
        self.lista_sugerencias.delete(0, tk.END)
        for s in sugerencias:               # sin [:10] — todos los resultados
            self.lista_sugerencias.insert(tk.END, s)

        altura = min(len(sugerencias), 8)   # hasta 8 filas; el resto con scroll
        self.lista_sugerencias.config(height=altura)

        self.frame_lista_wrap.pack(fill="x", pady=(4, 0))

    def seleccionar_de_lista(self):
        """El usuario elige un producto de la lista → carga confirmada."""
        if not self.lista_sugerencias.curselection():
            return
        seleccion = self.lista_sugerencias.get(self.lista_sugerencias.curselection()[0])
        self.entry_busqueda.delete(0, tk.END)
        self.entry_busqueda.insert(0, seleccion)
        self.ocultar_lista()
        self.cargar_producto_para_edicion(seleccion)

    def limpiar_filtros(self):
        self.entry_busqueda.delete(0, tk.END)
        self.combo_filtro_categ.current(0)
        self.combo_estado_stock.current(0)
        self.entry_precio_min.delete(0, tk.END)
        self.entry_precio_min.insert(0, "0")
        self.entry_precio_max.delete(0, tk.END)
        self.entry_precio_max.insert(0, "9999")
        self.ocultar_lista()
        self.desactivar_edicion()

    def ocultar_lista(self):
        self.frame_lista_wrap.pack_forget()

    def buscar_producto(self):
        """
        Búsqueda confirmada (botón Buscar o Enter).
        Aplica TODOS los filtros activos (texto, categoría, estado, precio).
        — 0 resultados  → aviso
        — 1 resultado   → carga directa (acción explícita del usuario)
        — 2+ resultados → muestra lista para que el usuario elija
        """
        texto_busqueda  = self.entry_busqueda.get().strip().lower()
        categoria_filtro = self.combo_filtro_categ.get()
        estado_filtro    = self.combo_estado_stock.get()

        try:
            precio_min = float(self.entry_precio_min.get() or 0)
            precio_max = float(self.entry_precio_max.get() or 9999)
        except ValueError:
            messagebox.showwarning("Error", "Ingrese valores numéricos válidos en el rango de precios.")
            return

        productos_encontrados = []

        for nombre, datos in self.productos.items():
            # Filtro texto
            if texto_busqueda:
                if not (texto_busqueda in nombre.lower() or
                        texto_busqueda in datos.get("codigo_barras", "").lower()):
                    continue

            # Filtro categoría
            if categoria_filtro != "Todas":
                if datos.get("categoria", "") != categoria_filtro:
                    continue

            # Filtro precio
            precio = datos.get("precio_venta", 0)
            if not (precio_min <= precio <= precio_max):
                continue

            # Filtro estado stock
            if estado_filtro != "Todos":
                stock     = datos["stock"]
                stock_min = datos["stock_minimo"]
                if estado_filtro == "Bajo Stock"   and stock >= stock_min:
                    continue
                if estado_filtro == "Stock Normal" and not (stock >= stock_min * 0.8 and stock < stock_min * 2):
                    continue
                if estado_filtro == "Stock Alto"   and stock < stock_min * 2:
                    continue

            productos_encontrados.append(nombre)

        productos_encontrados.sort()

        if len(productos_encontrados) == 0:
            messagebox.showwarning("No encontrado",
                                   "No hay productos que coincidan con los filtros aplicados.")
            self.desactivar_edicion()
            self.ocultar_lista()
        elif len(productos_encontrados) == 1 and texto_busqueda:
            # Un único resultado tras búsqueda explícita con texto → carga directa
            self.cargar_producto_para_edicion(productos_encontrados[0])
            self.ocultar_lista()
        else:
            # Varios resultados O búsqueda sin texto → mostrar lista para que el usuario elija
            self.mostrar_sugerencias(productos_encontrados)

    # ──────────────────────────────────────────────────────────────────────
    # CARGA / DESACTIVACIÓN DE PRODUCTO
    # ──────────────────────────────────────────────────────────────────────
    def cargar_producto_para_edicion(self, nombre_producto):
        self.producto_actual = nombre_producto
        datos = self.productos[nombre_producto]

        prov_id   = datos.get("proveedor_id", "")
        prov_data = self.proveedores.get(prov_id, {}) if prov_id else {}
        prov_info = (
            f"🏢 {prov_data.get('empresa','—')} | 📞 {prov_data.get('telefono','—')}"
            if prov_data else "—"
        )

        if self.rol_actual == "empleado":
            info_texto = (
                f"\n🏷️ PRODUCTO: {nombre_producto}\n"
                f"📊 Código: {datos.get('codigo_barras', 'N/A')}\n"
                f"📁 Categoría: {datos.get('categoria', 'N/A')}\n"
                f"📦 Stock Actual: {datos['stock']} unidades\n"
                f"⚠️ Stock Mínimo: {datos['stock_minimo']} unidades\n"
                f"🏢 Proveedor: {prov_info}\n"
            )
        else:
            info_texto = (
                f"\n🏷️ PRODUCTO: {nombre_producto}\n"
                f"📊 Código: {datos.get('codigo_barras', 'N/A')}\n"
                f"📁 Categoría: {datos.get('categoria', 'N/A')}\n"
                f"📦 Stock Actual: {datos['stock']} unidades\n"
                f"⚠️ Stock Mínimo: {datos['stock_minimo']} unidades\n"
                f"💵 Precio Venta: ${datos['precio_venta']:.2f}\n"
                f"💰 Precio Costo: ${datos['precio_costo']:.2f}\n"
                f"📈 Margen: {self.calcular_margen(datos['precio_venta'], datos['precio_costo']):.1f}%\n"
                f"💎 Valor en Inventario: ${datos['stock'] * datos['precio_costo']:.2f}\n"
                f"🏢 Proveedor: {prov_info}\n"
            )
        self.lbl_producto_info.config(text=info_texto, font=("Courier", 10),
                                      fg="black", justify="left")

        if self.tiene_permiso("editar"):
            self.val_stock.set(datos['stock'])
            self.entry_cantidad_op.config(state="normal")
            self.btn_entrada.config(state="normal")
            self.btn_salida.config(state="normal")
            self.spin_stock.config(state="normal")
            self.btn_ajuste_stock.config(state="normal")
            self.entry_ajuste_desc.config(state="normal")
            self.entry_ajuste_desc.delete(0, tk.END)

            self.entry_nombre_producto.config(state="normal")
            self.entry_nombre_producto.delete(0, tk.END)
            self.entry_nombre_producto.insert(0, nombre_producto)

            self.entry_precio_venta.config(state="normal")
            self.entry_precio_venta.delete(0, tk.END)
            self.entry_precio_venta.insert(0, str(datos['precio_venta']))

            self.entry_precio_costo.config(state="normal")
            self.entry_precio_costo.delete(0, tk.END)
            self.entry_precio_costo.insert(0, str(datos['precio_costo']))

            self.entry_stock_min.config(state="normal")
            self.entry_stock_min.delete(0, tk.END)
            self.entry_stock_min.insert(0, str(datos['stock_minimo']))

            # Cargar Código de barras
            self.entry_edit_codigo.config(state="normal")
            self.entry_edit_codigo.delete(0, tk.END)
            self.entry_edit_codigo.insert(0, datos.get('codigo_barras', ''))

            # Cargar Departamento (Categoría)
            self.combo_edit_cat.config(state="readonly")
            self.combo_edit_cat.set(datos.get('categoria', 'Otros'))

            # Cargar proveedor actual en el combo de edición
            self._refrescar_combo_proveedor(self.combo_edit_prov)
            prov_id_actual = datos.get("proveedor_id", "")
            if prov_id_actual and prov_id_actual in self.proveedores:
                prov_texto = f"{self.proveedores[prov_id_actual]['empresa']} ({prov_id_actual})"
                if prov_texto in self.combo_edit_prov['values']:
                    self.combo_edit_prov.set(prov_texto)
                else:
                    self.combo_edit_prov.current(0)
            else:
                # "Sin proveedor" option
                vals = list(self.combo_edit_prov['values'])
                sin_opcion = "— Sin proveedor —"
                if sin_opcion not in vals:
                    vals.insert(0, sin_opcion)
                    self.combo_edit_prov['values'] = vals
                self.combo_edit_prov.set(sin_opcion)
            self.combo_edit_prov.config(state="readonly")

            self.btn_guardar_config.config(state="normal")

            if self.tiene_permiso("eliminar"):
                self.btn_eliminar.config(state="normal")

    def desactivar_edicion(self):
        self.lbl_producto_info.config(text="Seleccione un producto para ver detalles",
                                      font=("Arial", 10, "italic"), fg="gray")
        self.producto_actual = None

        if self.tiene_permiso("editar"):
            self.entry_nombre_producto.config(state="disabled")
            self.entry_cantidad_op.config(state="disabled")
            self.btn_entrada.config(state="disabled")
            self.btn_salida.config(state="disabled")
            self.spin_stock.config(state="disabled")
            self.btn_ajuste_stock.config(state="disabled")
            self.entry_ajuste_desc.config(state="disabled")
            self.entry_ajuste_desc.delete(0, tk.END)
            self.entry_precio_venta.config(state="disabled")
            self.entry_precio_costo.config(state="disabled")
            self.entry_stock_min.config(state="disabled")
            self.combo_edit_prov.config(state="disabled")
            self.btn_guardar_config.config(state="disabled")
            self.entry_edit_codigo.config(state="disabled")
            self.combo_edit_cat.config(state="disabled")

            if self.tiene_permiso("eliminar"):
                self.btn_eliminar.config(state="disabled")

    # ──────────────────────────────────────────────────────────────────────
    # OPERACIONES DE INVENTARIO
    # ──────────────────────────────────────────────────────────────────────
    def operacion_entrada(self):
        if not self.producto_actual:
            return
        try:
            cantidad = int(self.entry_cantidad_op.get())
            if cantidad <= 0:
                raise ValueError("Cantidad debe ser positiva")
        except ValueError as e:
            messagebox.showerror("Error", f"Cantidad inválida:")
            return

        # Obtener proveedor sugerido del producto (si tiene)
        prov_id_actual = self.productos[self.producto_actual].get("proveedor_id", "")
        prov_sugerido  = (f"{self.proveedores[prov_id_actual]['empresa']} ({prov_id_actual})"
                          if prov_id_actual and prov_id_actual in self.proveedores else "")

        # Diálogo para confirmar proveedor de esta entrega
        dlg = tk.Toplevel(self.root)
        dlg.title("Registrar Entrada de Mercancía")
        dlg.geometry("420x220")
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text=f"Entrada: {self.producto_actual}",
                 font=("Arial", 11, "bold")).pack(pady=(12, 4))
        tk.Label(dlg, text=f"Cantidad: +{cantidad} unidades",
                 font=("Arial", 10), fg="#388E3C").pack()

        tk.Label(dlg, text="Proveedor que entrega:",
                 font=("Arial", 10)).pack(pady=(12, 3))
        combo_prov_ent = ttk.Combobox(dlg, state="readonly", width=35)
        activos = [f"{d['empresa']} ({pid})"
                   for pid, d in sorted(self.proveedores.items(),
                                        key=lambda x: x[1].get('empresa', ''))
                   if d.get('activo', True)]
        combo_prov_ent['values'] = activos if activos else ["— Sin proveedores —"]
        if prov_sugerido and prov_sugerido in activos:
            combo_prov_ent.set(prov_sugerido)
        elif activos:
            combo_prov_ent.current(0)
        combo_prov_ent.pack(pady=3)

        resultado = {"ok": False}

        def confirmar():
            resultado["ok"]   = True
            resultado["prov"] = combo_prov_ent.get()
            dlg.destroy()

        def cancelar():
            dlg.destroy()

        fb = tk.Frame(dlg)
        fb.pack(pady=10)
        tk.Button(fb, text="✅ Confirmar Entrada", command=confirmar,
                  bg="#4CAF50", fg="white", width=16).pack(side="left", padx=5)
        tk.Button(fb, text="❌ Cancelar", command=cancelar,
                  bg="#9E9E9E", fg="white", width=12).pack(side="left", padx=5)

        dlg.wait_window()

        if not resultado.get("ok"):
            return

        prov_texto = resultado.get("prov", "")
        prov_id    = self._id_desde_combo_prov(prov_texto) or ""
        prov_nom   = (self.proveedores[prov_id]["empresa"]
                      if prov_id and prov_id in self.proveedores else prov_texto)

        self.productos[self.producto_actual]['stock'] += cantidad
        self.registrar_historial(
            f"ENTRADA: {self.producto_actual} (+{cantidad} unidades)"
            + (f" | Proveedor: {prov_nom}" if prov_nom else "")
        )
        self.finalizar_operacion(f"Entrada registrada: +{cantidad} unidades")

    def operacion_salida(self):
        if not self.producto_actual:
            return
        try:
            cantidad     = int(self.entry_cantidad_op.get())
            stock_actual = self.productos[self.producto_actual]['stock']
            if cantidad <= 0:
                raise ValueError("Cantidad debe ser positiva")
            if cantidad > stock_actual:
                raise ValueError(f"Stock insuficiente. Disponible: {stock_actual}")
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        # Diálogo: tipo de salida (venta/merma/devolución)
        dlg = tk.Toplevel(self.root)
        dlg.title("Registrar Salida")
        dlg.geometry("400x250")
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text=f"Salida: {self.producto_actual}",
                 font=("Arial", 11, "bold")).pack(pady=(12, 4))
        tk.Label(dlg, text=f"Cantidad: -{cantidad} unidades",
                 font=("Arial", 10), fg="#d32f2f").pack()

        tk.Label(dlg, text="Tipo de salida:", font=("Arial", 10)).pack(pady=(12, 3))
        tipo_var = tk.StringVar(value="Venta")
        frame_tipos = tk.Frame(dlg)
        frame_tipos.pack()
        for t in ["Venta", "Merma / Caducidad", "Devolución a Proveedor", "Otro"]:
            tk.Radiobutton(frame_tipos, text=t, variable=tipo_var,
                           value=t, font=("Arial", 9)).pack(anchor="w", padx=20)

        resultado = {"ok": False}

        def confirmar():
            resultado["ok"]   = True
            resultado["tipo"] = tipo_var.get()
            dlg.destroy()

        def cancelar():
            dlg.destroy()

        fb = tk.Frame(dlg)
        fb.pack(pady=8)
        tk.Button(fb, text="✅ Confirmar Salida", command=confirmar,
                  bg="#f44336", fg="white", width=16).pack(side="left", padx=5)
        tk.Button(fb, text="❌ Cancelar", command=cancelar,
                  bg="#9E9E9E", fg="white", width=12).pack(side="left", padx=5)

        dlg.wait_window()
        if not resultado.get("ok"):
            return

        tipo = resultado.get("tipo", "Venta")
        prov_id  = self.productos[self.producto_actual].get("proveedor_id", "")
        prov_nom = self._nombre_proveedor(prov_id) if prov_id else ""

        self.productos[self.producto_actual]['stock'] -= cantidad

        detalle = f"SALIDA [{tipo}]: {self.producto_actual} (-{cantidad} unidades)"
        if tipo == "Devolución a Proveedor" and prov_nom:
            detalle += f" | Proveedor: {prov_nom}"
        self.registrar_historial(detalle)
        self.finalizar_operacion(f"Salida registrada: -{cantidad} unidades ({tipo})")

    def ajuste_manual_stock(self):
        if not self.producto_actual:
            return

        descripcion = self.entry_ajuste_desc.get().strip()
        if not descripcion:
            messagebox.showerror(
                "Descripción obligatoria",
                "Debe ingresar un motivo o descripción para justificar el ajuste manual.\n\n"
                "Ejemplo: 'Corrección por conteo físico', 'Merma por caducidad', etc."
            )
            self.entry_ajuste_desc.focus()
            return

        if not messagebox.askyesno("Confirmar Ajuste",
                                   "¿Está seguro de realizar un ajuste manual de stock?\n"
                                   "Esta operación quedará registrada en el historial."):
            return

        try:
            nuevo_stock = self.val_stock.get()
            if nuevo_stock < 0:
                raise ValueError("El stock no puede ser negativo")
            stock_anterior = self.productos[self.producto_actual]['stock']
            self.productos[self.producto_actual]['stock'] = nuevo_stock
            diferencia = nuevo_stock - stock_anterior
            signo = "+" if diferencia >= 0 else ""
            self.registrar_historial(
                f"AJUSTE MANUAL: {self.producto_actual} ({stock_anterior} → {nuevo_stock}) {signo}{diferencia}"
                f" | Motivo: {descripcion}"
            )
            self.finalizar_operacion("Ajuste manual aplicado correctamente")
        except ValueError as e:
            messagebox.showerror("Error", str(e))

    def guardar_configuracion_producto(self):
        if not self.producto_actual:
            return

        # ── FASE 1: Leer y validar campos (fuera del diálogo) ─────────────
        try:
            nombre_nuevo = self.entry_nombre_producto.get().strip()
            precio_venta = float(self.entry_precio_venta.get())
            precio_costo = float(self.entry_precio_costo.get())
            stock_min    = int(self.entry_stock_min.get())
            nuevo_codigo = self.entry_edit_codigo.get().strip()
            nueva_cat    = self.combo_edit_cat.get()
        except ValueError:
            messagebox.showerror("Error", "Verifique que los campos numéricos sean válidos.")
            return

        for prod, d in self.productos.items():
            if prod != self.producto_actual and d.get('codigo_barras') == nuevo_codigo and nuevo_codigo != "":
                messagebox.showerror("Error", "El código '" + nuevo_codigo + "' ya pertenece al producto '" + prod + "'")
                return
        if not nombre_nuevo:
            messagebox.showerror("Error", "El nombre del producto no puede estar vacío")
            return
        if precio_venta <= 0 or precio_costo <= 0:
            messagebox.showerror("Error", "Los precios deben ser mayores a cero")
            return
        if precio_venta < precio_costo:
            if not messagebox.askyesno("Advertencia",
                                       "El precio de venta es menor al precio de costo.\n¿Desea continuar?"):
                return
        if stock_min < 0:
            messagebox.showerror("Error", "El stock mínimo no puede ser negativo")
            return
        if nombre_nuevo != self.producto_actual and nombre_nuevo in self.productos:
            messagebox.showerror("Error", "Ya existe un producto con el nombre '" + nombre_nuevo + "'")
            return

        datos_anteriores = self.productos[self.producto_actual].copy()
        prov_edit_texto  = self.combo_edit_prov.get()
        nuevo_prov_id    = self._id_desde_combo_prov(prov_edit_texto) or ""
        codigo_anterior  = datos_anteriores.get("codigo_barras", "")

        # ── FASE 2: Diálogo de justificación si el código cambió ───────────
        justificacion_codigo = None
        if nuevo_codigo != codigo_anterior:
            justificacion_codigo = self._pedir_justificacion_codigo(
                codigo_anterior, nuevo_codigo)
            if justificacion_codigo is None:
                return  # Usuario canceló — no aplicar nada

        # ── FASE 3: Aplicar todos los cambios ──────────────────────────────
        cambios = []

        if nombre_nuevo != self.producto_actual:
            self.productos[nombre_nuevo] = self.productos.pop(self.producto_actual)
            cambios.append("Nombre: '" + self.producto_actual + "' -> '" + nombre_nuevo + "'")
            self.producto_actual = nombre_nuevo

        self.productos[self.producto_actual]['precio_venta']  = precio_venta
        self.productos[self.producto_actual]['precio_costo']  = precio_costo
        self.productos[self.producto_actual]['stock_minimo']  = stock_min
        self.productos[self.producto_actual]['codigo_barras'] = nuevo_codigo
        self.productos[self.producto_actual]['categoria']     = nueva_cat

        # Registrar cambio de código con su justificación (entrada separada y destacada)
        if justificacion_codigo is not None:
            cod_ant_log = codigo_anterior if codigo_anterior else "(sin codigo)"
            cod_nvo_log = nuevo_codigo    if nuevo_codigo    else "(sin codigo)"
            self.registrar_historial(
                "CAMBIO CODIGO DE BARRAS: " + self.producto_actual +
                " | Anterior: " + cod_ant_log +
                " | Nuevo: "    + cod_nvo_log +
                " | Justificacion: " + justificacion_codigo
            )

        # Actualizar proveedor
        prov_anterior_id = datos_anteriores.get('proveedor_id', '')
        if nuevo_prov_id != prov_anterior_id:
            self.productos[self.producto_actual]['proveedor_id'] = nuevo_prov_id
            prov_ant_nom = self._nombre_proveedor(prov_anterior_id) if prov_anterior_id else "—"
            prov_nvo_nom = self._nombre_proveedor(nuevo_prov_id)    if nuevo_prov_id    else "—"
            cambios.append("Proveedor: '" + prov_ant_nom + "' -> '" + prov_nvo_nom + "'")

        if datos_anteriores['precio_venta'] != precio_venta:
            cambios.append("P.Venta: $" + str(datos_anteriores['precio_venta']) + " -> $" + str(precio_venta))
        if datos_anteriores['precio_costo'] != precio_costo:
            cambios.append("P.Costo: $" + str(datos_anteriores['precio_costo']) + " -> $" + str(precio_costo))
        if datos_anteriores['stock_minimo'] != stock_min:
            cambios.append("Stock Min: " + str(datos_anteriores['stock_minimo']) + " -> " + str(stock_min))

        if cambios:
            self.registrar_historial("ACTUALIZACION: " + self.producto_actual + " - " + ', '.join(cambios))
        self.finalizar_operacion("Configuración actualizada correctamente")

    def _pedir_justificacion_codigo(self, codigo_anterior, nuevo_codigo):
        """
        Muestra un diálogo modal que pide justificación para el cambio de código de barras.
        Devuelve el texto de justificación si el usuario confirma, o None si cancela.
        Este método es independiente del try/except de guardar_configuracion_producto,
        lo que evita que wait_window interfiera con el manejo de excepciones.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("Cambio de Código de Barras — Justificación")
        dlg.geometry("520x430")
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text="⚠️  Cambio de Código de Barras",
                 font=("Arial", 13, "bold"), fg="#E65100").pack(pady=(14, 4))
        tk.Label(dlg, text=self.producto_actual,
                 font=("Arial", 10), fg="#424242").pack(pady=(0, 8))

        # Detalle del cambio
        frame_detalle = tk.LabelFrame(dlg, text="📊  Detalle del cambio", padx=12, pady=8)
        frame_detalle.pack(fill="x", padx=15, pady=(0, 6))
        cod_ant_txt = codigo_anterior if codigo_anterior else "(sin código)"
        cod_nvo_txt = nuevo_codigo    if nuevo_codigo    else "(sin código)"
        tk.Label(frame_detalle, text="Código anterior:  " + cod_ant_txt,
                 font=("Courier", 10), fg="#B71C1C", anchor="w").pack(fill="x", padx=5)
        tk.Label(frame_detalle, text="Código nuevo:     " + cod_nvo_txt,
                 font=("Courier", 10, "bold"), fg="#1B5E20", anchor="w").pack(fill="x", padx=5)

        # Advertencia de impacto
        frame_imp = tk.LabelFrame(dlg, text="🗄️  Impacto en la Base de Datos", padx=12, pady=8)
        frame_imp.pack(fill="x", padx=15, pady=(0, 6))
        tk.Label(frame_imp,
                 text=("El código de barras identifica el producto en:\n"
                       "  - Búsquedas y filtros del inventario.\n"
                       "  - Escaneo en el Punto de Venta.\n"
                       "  - Alertas de stock bajo y reportes.\n\n"
                       "Asegúrese de que el nuevo código sea correcto y único."),
                 font=("Arial", 9), fg="#5D4037", justify="left").pack(anchor="w")

        # Justificación obligatoria
        frame_just = tk.LabelFrame(dlg, text="📋  Justificación del cambio  * (obligatorio)",
                                   padx=12, pady=10)
        frame_just.pack(fill="x", padx=15, pady=(0, 10))

        tk.Label(frame_just,
                 text="Describa el motivo del cambio antes de confirmar:",
                 font=("Arial", 9, "bold"), fg="#1A237E").pack(anchor="w", pady=(0, 4))
        tk.Label(frame_just,
                 text="Ej: 'Código erróneo en el alta', 'Actualización de EAN', 'Cambio de proveedor'",
                 font=("Arial", 8), fg="#757575").pack(anchor="w")

        entry_just = tk.Entry(frame_just, font=("Arial", 11), bg="#FFFDE7",
                              relief="solid", bd=2, insertbackground="#1A237E")
        entry_just.pack(fill="x", pady=(6, 4), ipady=6)

        tk.Label(frame_just,
                 text="Presione el botón  ✅ Confirmar  o la tecla Enter para guardar.",
                 font=("Arial", 8, "italic"), fg="#555555").pack(anchor="w")

        entry_just.focus()

        resultado = {"valor": None}

        def confirmar():
            just = entry_just.get().strip()
            if not just:
                messagebox.showerror("Justificación obligatoria",
                                     "Debe escribir una justificación antes de confirmar el cambio.",
                                     parent=dlg)
                entry_just.focus()
                return
            resultado["valor"] = just
            dlg.destroy()

        def cancelar():
            dlg.destroy()

        entry_just.bind("<Return>", lambda e: confirmar())

        frame_btns = tk.Frame(dlg)
        frame_btns.pack(pady=(4, 14))
        tk.Button(frame_btns, text="✅  Confirmar Cambio de Código", command=confirmar,
                  bg="#1565C0", fg="white", font=("Arial", 10, "bold"),
                  width=26, height=2, cursor="hand2").pack(side="left", padx=8)
        tk.Button(frame_btns, text="❌  Cancelar", command=cancelar,
                  bg="#757575", fg="white", font=("Arial", 10, "bold"),
                  width=14, height=2, cursor="hand2").pack(side="left", padx=8)

        dlg.wait_window()
        return resultado["valor"]

    def añadir_nuevo_producto(self):
        try:
            nombre       = self.entry_nuevo_nom.get().strip()
            codigo       = self.entry_nuevo_codigo.get().strip()
            stock        = int(self.entry_nuevo_stock.get())
            stock_min    = int(self.entry_nuevo_min.get())
            precio_venta = float(self.entry_nuevo_pventa.get())
            precio_costo = float(self.entry_nuevo_pcosto.get())
            categoria    = self.combo_nuevo_cat.get()
            prov_texto   = self.combo_nuevo_prov.get() if self.tiene_permiso("crear") else ""
            prov_id      = self._id_desde_combo_prov(prov_texto) or ""

            if not nombre:
                raise ValueError("El nombre del producto es obligatorio")
            if nombre in self.productos:
                raise ValueError("Ya existe un producto con ese nombre")
            for prod, datos in self.productos.items():
                if datos.get('codigo_barras') == codigo and codigo:
                    raise ValueError(f"El código de barras ya está asignado a '{prod}'")
            if stock < 0 or stock_min < 0:
                raise ValueError("El stock no puede ser negativo")
            if precio_venta <= 0 or precio_costo <= 0:
                raise ValueError("Los precios deben ser mayores a cero")

            self.productos[nombre] = {
                "stock": stock, "stock_minimo": stock_min,
                "precio_venta": precio_venta, "precio_costo": precio_costo,
                "categoria": categoria, "codigo_barras": codigo,
                "proveedor_id": prov_id
            }
            prov_nom = self._nombre_proveedor(prov_id) if prov_id else ""
            self.registrar_historial(
                f"NUEVO PRODUCTO: {nombre} | Cat: {categoria} | Stock: {stock} | P.Venta: ${precio_venta:.2f}"
                + (f" | Proveedor: {prov_nom}" if prov_nom else "")
            )
            for w in [self.entry_nuevo_nom, self.entry_nuevo_codigo, self.entry_nuevo_stock,
                      self.entry_nuevo_min, self.entry_nuevo_pventa, self.entry_nuevo_pcosto]:
                w.delete(0, tk.END)
            self.combo_nuevo_cat.current(0)
            self._refrescar_combo_proveedor(self.combo_nuevo_prov)
            self.finalizar_operacion(f"Producto '{nombre}' registrado exitosamente")
        except ValueError as e:
            messagebox.showerror("Error", str(e))

    def eliminar_producto(self):
        if not self.producto_actual:
            return
        if not messagebox.askyesno("Confirmar Eliminación",
                                   f"¿Está seguro de eliminar '{self.producto_actual}'?\n\nEsta acción no se puede deshacer."):
            return
        nombre = self.producto_actual
        datos  = self.productos[nombre]
        self.registrar_historial(
            f"ELIMINADO: {nombre} | Stock: {datos['stock']} | Valor: ${datos['stock'] * datos['precio_costo']:.2f}"
        )
        del self.productos[nombre]
        self.finalizar_operacion(f"Producto '{nombre}' eliminado del sistema")

    def finalizar_operacion(self, mensaje):
        """
        Guarda en Firebase de forma asíncrona y, una vez confirmado el guardado,
        refresca la UI y muestra el mensaje de éxito.
        """
        if self._sincronizando:
            return  # Guardia anti-doble-clic

        def post_guardado():
            self.actualizar_vistas()
            if self.tiene_permiso("reportes"):
                self.actualizar_reportes()
            messagebox.showinfo("Operación Exitosa", mensaje)
            if self.tiene_permiso("editar"):
                self.entry_cantidad_op.delete(0, tk.END)
                self.entry_ajuste_desc.delete(0, tk.END)
            if self.producto_actual and self.producto_actual in self.productos:
                self.cargar_producto_para_edicion(self.producto_actual)
            else:
                self.entry_busqueda.delete(0, tk.END)
                self.desactivar_edicion()

        self.guardar_datos(callback=post_guardado)

    # ──────────────────────────────────────────────────────────────────────
    # ACTUALIZACIÓN DE VISTAS
    # ──────────────────────────────────────────────────────────────────────
    def actualizar_vistas(self):
        for item in self.tree_stock.get_children():
            self.tree_stock.delete(item)
        for item in self.tree_bajo.get_children():
            self.tree_bajo.delete(item)

        filtro_cat = self.combo_filtro_cat.get()

        for nombre, datos in sorted(self.productos.items()):
            if filtro_cat != "Todas" and datos.get('categoria', '') != filtro_cat:
                continue

            stock        = datos['stock']
            stock_min    = datos['stock_minimo']
            precio_venta = datos['precio_venta']
            precio_costo = datos['precio_costo']
            margen       = self.calcular_margen(precio_venta, precio_costo)
            valor_inv    = stock * precio_costo

            if stock == 0:            estado = "🔴 SIN STOCK"
            elif stock <= stock_min:  estado = "⚠️ CRÍTICO"
            elif stock <= stock_min * 1.5: estado = "⚡ BAJO"
            else:                     estado = "✅ OK"

            if self.rol_actual == "empleado":
                self.tree_stock.insert("", tk.END, values=(
                    datos.get('codigo_barras', 'N/A'), nombre,
                    datos.get('categoria', 'N/A'), stock, stock_min, estado
                ))
            else:
                self.tree_stock.insert("", tk.END, values=(
                    datos.get('codigo_barras', 'N/A'), nombre,
                    datos.get('categoria', 'N/A'), stock, stock_min,
                    f"${precio_venta:.2f}", f"${precio_costo:.2f}",
                    f"{margen:.1f}%", f"${valor_inv:.2f}", estado
                ))

            if stock <= stock_min:
                prov_id   = datos.get("proveedor_id", "")
                prov_data = self.proveedores.get(prov_id, {}) if prov_id else {}
                prov_nom  = prov_data.get("empresa",  "—")
                prov_con  = prov_data.get("contacto", "—")
                prov_tel  = prov_data.get("telefono", "—")
                self.tree_bajo.insert("", tk.END, values=(
                    datos.get('codigo_barras', 'N/A'), nombre,
                    datos.get('categoria', 'N/A'), stock, stock_min,
                    stock_min - stock + 10,
                    prov_nom, prov_con, prov_tel
                ))

    def actualizar_reportes(self):
        if not self.tiene_permiso("reportes"):
            return

        total_productos = len(self.productos)
        valor_total_inv = valor_total_venta = productos_criticos = 0
        margenes = []
        stats_categoria = {}

        for nombre, datos in self.productos.items():
            stock        = datos['stock']
            precio_costo = datos['precio_costo']
            precio_venta = datos['precio_venta']
            categoria    = datos.get('categoria', 'Otros')

            valor_total_inv   += stock * precio_costo
            valor_total_venta += stock * precio_venta
            if stock <= datos['stock_minimo']:
                productos_criticos += 1
            margenes.append(self.calcular_margen(precio_venta, precio_costo))

            if categoria not in stats_categoria:
                stats_categoria[categoria] = {'cantidad': 0, 'stock_total': 0, 'valor_total': 0}
            stats_categoria[categoria]['cantidad']    += 1
            stats_categoria[categoria]['stock_total'] += stock
            stats_categoria[categoria]['valor_total'] += stock * precio_costo

        margen_promedio    = sum(margenes) / len(margenes) if margenes else 0
        ganancia_potencial = valor_total_venta - valor_total_inv

        self.lbl_total_productos.config(text=f"📦 Total de Productos: {total_productos}", fg="#1976D2")
        self.lbl_valor_inventario.config(
            text=f"💰 Valor del Inventario (Costo): ${valor_total_inv:,.2f} | Potencial Venta: ${valor_total_venta:,.2f}",
            fg="#388E3C")
        self.lbl_productos_criticos.config(
            text=f"⚠️ Productos en Stock Crítico: {productos_criticos}",
            fg="#D32F2F" if productos_criticos > 0 else "#388E3C")
        self.lbl_margen_promedio.config(
            text=f"📈 Margen Promedio: {margen_promedio:.1f}% | Ganancia Potencial: ${ganancia_potencial:,.2f}",
            fg="#F57C00")

        for item in self.tree_cat_stats.get_children():
            self.tree_cat_stats.delete(item)
        for categoria, stats in sorted(stats_categoria.items()):
            self.tree_cat_stats.insert("", tk.END, values=(
                categoria, stats['cantidad'], stats['stock_total'],
                f"${stats['valor_total']:,.2f}"
            ))

    def actualizar_historial(self):
        try:
            limite = int(self.spin_historial_limit.get())
        except Exception:
            limite = 100

        entradas_visibles = (
            [e for e in self.historial_persistente if self.nombre_completo in e]
            if self.rol_actual == "empleado"
            else self.historial_persistente
        )
        self.txt_historial.config(state="normal")
        self.txt_historial.delete("1.0", tk.END)
        for entrada in entradas_visibles[:limite]:
            self.txt_historial.insert(tk.END, entrada)
        self.txt_historial.config(state="disabled")

    # ──────────────────────────────────────────────────────────────────────
    # FUNCIONES AUXILIARES
    # ──────────────────────────────────────────────────────────────────────
    def calcular_margen(self, precio_venta, precio_costo):
        if precio_costo == 0:
            return 0
        return ((precio_venta - precio_costo) / precio_costo) * 100

    def registrar_historial(self, mensaje):
        timestamp = datetime.now().strftime("[%d/%m/%Y %H:%M:%S]")
        linea = f"{timestamp} {self.nombre_completo} ({self.rol_actual}) → {mensaje}\n"
        self.historial_persistente.insert(0, linea)
        if len(self.historial_persistente) > 1000:
            self.historial_persistente = self.historial_persistente[:1000]
        if self.rol_actual != "empleado" or self.nombre_completo in linea:
            self.txt_historial.config(state="normal")
            self.txt_historial.insert("1.0", linea)
            self.txt_historial.config(state="disabled")

    def exportar_reporte(self):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            nombre_archivo = f"reporte_inventario_{timestamp}.txt"
            with open(nombre_archivo, "w", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write("REPORTE DE INVENTARIO\n")
                f.write(f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
                f.write(f"Usuario: {self.nombre_completo} ({self.rol_actual})\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"Total de productos: {len(self.productos)}\n\n")
                for nombre, datos in sorted(self.productos.items()):
                    f.write(f"\nProducto: {nombre}\n")
                    f.write(f"  Código: {datos.get('codigo_barras', 'N/A')}\n")
                    f.write(f"  Categoría: {datos.get('categoria', 'N/A')}\n")
                    f.write(f"  Stock: {datos['stock']} (Mínimo: {datos['stock_minimo']})\n")
                    f.write(f"  Precio Venta: ${datos['precio_venta']:.2f}\n")
                    f.write(f"  Precio Costo: ${datos['precio_costo']:.2f}\n")
                    f.write(f"  Margen: {self.calcular_margen(datos['precio_venta'], datos['precio_costo']):.1f}%\n")
                    f.write(f"  Valor en Inventario: ${datos['stock'] * datos['precio_costo']:.2f}\n")
                    f.write("-" * 80 + "\n")
            messagebox.showinfo("Reporte Exportado", f"Reporte guardado como:\n{nombre_archivo}")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo exportar el reporte: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # GESTIÓN DE CONTRASEÑAS
    # ──────────────────────────────────────────────────────────────────────
    def ventana_restablecer_password_admin(self):
        if not self.tiene_permiso("usuarios"):
            messagebox.showerror("Acceso Denegado", "Solo el administrador puede gestionar contraseñas.")
            return

        sistema = SistemaUsuarios()
        lista_usuarios = list(sistema.usuarios.keys())
        if not lista_usuarios:
            messagebox.showinfo("Sin usuarios", "No hay usuarios disponibles.")
            return

        ventana = tk.Toplevel(self.root)
        ventana.title("🔑 Restablecer Contraseña de Usuario")
        ventana.geometry("520x420")
        ventana.resizable(False, False)
        ventana.grab_set()

        tk.Label(ventana, text="🔑 Restablecer Contraseña", font=("Arial", 14, "bold")).pack(pady=15)
        tk.Label(ventana, text="⚠️ Esta acción requiere autenticación de administrador",
                 font=("Arial", 9), fg="#e65100").pack()

        frame_objetivo = tk.LabelFrame(ventana, text="Usuario a modificar", padx=15, pady=10)
        frame_objetivo.pack(pady=8, padx=20, fill="x")

        tk.Label(frame_objetivo, text="Seleccionar usuario:").pack(anchor="w")
        combo_usuario = ttk.Combobox(frame_objetivo, values=lista_usuarios, state="readonly", width=28)
        combo_usuario.pack(anchor="w", pady=(3, 5))
        combo_usuario.current(0)

        tk.Label(frame_objetivo, text="Nueva contraseña (mín. 6 caracteres):").pack(anchor="w")
        entry_nueva = tk.Entry(frame_objetivo, show="●", font=("Arial", 11), width=30)
        entry_nueva.pack(anchor="w", pady=(3, 5))

        tk.Label(frame_objetivo, text="Confirmar nueva contraseña:").pack(anchor="w")
        entry_confirmar = tk.Entry(frame_objetivo, show="●", font=("Arial", 11), width=30)
        entry_confirmar.pack(anchor="w", pady=(3, 0))

        frame_auth = tk.LabelFrame(ventana, text="🔐 Confirmar identidad (Administrador)", padx=15, pady=10)
        frame_auth.pack(pady=5, padx=20, fill="x")

        tk.Label(frame_auth, text=f"Su contraseña ({self.usuario_actual}):").pack(anchor="w")
        entry_admin_pass = tk.Entry(frame_auth, show="●", font=("Arial", 11), width=30)
        entry_admin_pass.pack(anchor="w", pady=(3, 0))
        entry_admin_pass.focus()

        def ejecutar_restablecimiento():
            usuario_objetivo = combo_usuario.get()
            nueva    = entry_nueva.get()
            confirmar = entry_confirmar.get()
            admin_pass = entry_admin_pass.get()

            if not usuario_objetivo or not nueva or not confirmar or not admin_pass:
                messagebox.showwarning("Campos vacíos", "Todos los campos son obligatorios.", parent=ventana)
                return
            if len(nueva) < 6:
                messagebox.showerror("Error", "La nueva contraseña debe tener al menos 6 caracteres.", parent=ventana)
                return
            if nueva != confirmar:
                messagebox.showerror("Error", "La nueva contraseña y su confirmación no coinciden.", parent=ventana)
                entry_nueva.delete(0, tk.END)
                entry_confirmar.delete(0, tk.END)
                entry_nueva.focus()
                return

            exito, mensaje = sistema.restablecer_password(
                self.usuario_actual, admin_pass, usuario_objetivo, nueva)
            if exito:
                self.registrar_historial(
                    f"SEGURIDAD: Contraseña de '{usuario_objetivo}' restablecida por administrador")
                messagebox.showinfo("Éxito", mensaje, parent=ventana)
                ventana.destroy()
            else:
                messagebox.showerror("Error", mensaje, parent=ventana)
                entry_admin_pass.delete(0, tk.END)
                entry_admin_pass.focus()

        frame_botones = tk.Frame(ventana)
        frame_botones.pack(pady=12)
        tk.Button(frame_botones, text="🔒 Restablecer Contraseña", command=ejecutar_restablecimiento,
                  bg="#d32f2f", fg="white", font=("Arial", 10, "bold"),
                  width=20, height=2).pack(side="left", padx=5)
        tk.Button(frame_botones, text="❌ Cancelar", command=ventana.destroy,
                  bg="#757575", fg="white", font=("Arial", 10, "bold"),
                  width=20, height=2).pack(side="left", padx=5)

    def ventana_usuarios(self):
        if not self.tiene_permiso("usuarios"):
            messagebox.showerror("Acceso Denegado", "No tiene permisos para gestionar usuarios")
            return

        ventana = tk.Toplevel(self.root)
        ventana.title("Gestión de Usuarios")
        ventana.geometry("560x480")
        ventana.grab_set()

        tk.Label(ventana, text="Gestión de Usuarios", font=("Arial", 14, "bold")).pack(pady=10)

        frame_form = tk.LabelFrame(ventana, text="Nuevo Usuario", padx=20, pady=10)
        frame_form.pack(pady=10, padx=20, fill="x")

        tk.Label(frame_form, text="Usuario:").grid(row=0, column=0, sticky="w", pady=5)
        entry_user = tk.Entry(frame_form, width=20)
        entry_user.grid(row=0, column=1, pady=5)

        tk.Label(frame_form, text="Contraseña:").grid(row=1, column=0, sticky="w", pady=5)
        entry_pass = tk.Entry(frame_form, width=20, show="●")
        entry_pass.grid(row=1, column=1, pady=5)

        tk.Label(frame_form, text="Nombre Completo:").grid(row=2, column=0, sticky="w", pady=5)
        entry_nombre = tk.Entry(frame_form, width=20)
        entry_nombre.grid(row=2, column=1, pady=5)

        tk.Label(frame_form, text="Rol:").grid(row=3, column=0, sticky="w", pady=5)
        combo_rol = ttk.Combobox(frame_form, values=["administrador", "gerente", "empleado"],
                                  state="readonly", width=18)
        combo_rol.grid(row=3, column=1, pady=5)
        combo_rol.current(2)

        def crear_usuario():
            usuario  = entry_user.get().strip()
            password = entry_pass.get()
            nombre   = entry_nombre.get().strip()
            rol      = combo_rol.get()
            if not usuario or not password or not nombre:
                messagebox.showerror("Error", "Todos los campos son obligatorios")
                return
            if len(password) < 6:
                messagebox.showerror("Error", "La contraseña debe tener al menos 6 caracteres")
                return
            sistema_usuarios = SistemaUsuarios()
            exito, mensaje = sistema_usuarios.agregar_usuario(usuario, password, rol, nombre)
            if exito:
                self.registrar_historial(f"USUARIO CREADO: '{usuario}' con rol '{rol}'")
                messagebox.showinfo("Éxito", mensaje)
                entry_user.delete(0, tk.END)
                entry_pass.delete(0, tk.END)
                entry_nombre.delete(0, tk.END)
                combo_rol.current(2)
                refrescar_lista()
            else:
                messagebox.showerror("Error", mensaje)

        tk.Button(frame_form, text="Crear Usuario", command=crear_usuario,
                  bg="#4CAF50", fg="white").grid(row=4, column=0, columnspan=2, pady=10)

        frame_lista = tk.LabelFrame(ventana, text="Usuarios Registrados (las contraseñas no se muestran)",
                                     padx=10, pady=10)
        frame_lista.pack(pady=5, padx=20, fill="both", expand=True)

        tree_usuarios = ttk.Treeview(frame_lista, columns=("Usuario", "Nombre", "Rol"),
                                      show="headings", height=7)
        tree_usuarios.heading("Usuario", text="Usuario")
        tree_usuarios.heading("Nombre",  text="Nombre Completo")
        tree_usuarios.heading("Rol",     text="Rol")
        tree_usuarios.column("Usuario", width=110)
        tree_usuarios.column("Nombre",  width=180)
        tree_usuarios.column("Rol",     width=120)

        scrollbar_tree = ttk.Scrollbar(frame_lista, orient="vertical", command=tree_usuarios.yview)
        tree_usuarios.configure(yscrollcommand=scrollbar_tree.set)
        tree_usuarios.pack(side="left", fill="both", expand=True)
        scrollbar_tree.pack(side="right", fill="y")

        def refrescar_lista():
            for item in tree_usuarios.get_children():
                tree_usuarios.delete(item)
            sistema_temp = SistemaUsuarios()
            for usr, datos in sistema_temp.usuarios.items():
                tree_usuarios.insert("", tk.END, values=(
                    usr, datos.get("nombre_completo", "N/A"), datos.get("rol", "N/A")
                ))

        refrescar_lista()
        tk.Label(ventana,
                 text="ℹ️  Para cambiar contraseñas use: Administración → Gestionar Contraseñas",
                 font=("Arial", 8), fg="#1565C0").pack(pady=(0, 8))

    def cerrar_sesion(self):
        if messagebox.askyesno("Cerrar Sesión", "¿Desea cerrar la sesión actual?"):
            def post_guardado():
                self.root.destroy()
                mostrar_login()
            self.guardar_datos(callback=post_guardado)


# ==========================================
# VENTANA DE LOGIN
# ==========================================
def mostrar_login():
    sistema_usuarios = SistemaUsuarios()

    def validar_login(event=None):
        usuario  = entry_usuario.get().strip()
        password = entry_contrasena.get()
        if not usuario or not password:
            messagebox.showwarning("Datos Incompletos", "Ingrese usuario y contraseña")
            return
        valido, datos_usuario = sistema_usuarios.validar_credenciales(usuario, password)
        if valido:
            login_window.destroy()
            root = tk.Tk()
            InventarioApp(root, {
                "usuario":         usuario,
                "rol":             datos_usuario["rol"],
                "nombre_completo": datos_usuario["nombre_completo"]
            })
            root.mainloop()
        else:
            messagebox.showerror("Error de Acceso", "Usuario o contraseña incorrectos")
            entry_contrasena.delete(0, tk.END)

    login_window = tk.Tk()
    login_window.title("Sistema de Inventario - Acceso")
    login_window.geometry("440x420")
    login_window.resizable(False, False)

    tk.Label(login_window, text="🏪 SISTEMA DE INVENTARIO",
             font=("Arial", 16, "bold"), fg="#1976D2").pack(pady=20)
    tk.Label(login_window, text="Supermercado", font=("Arial", 12), fg="#666").pack()

    frame_login = tk.LabelFrame(login_window, text="Iniciar Sesión", padx=30, pady=20)
    frame_login.pack(pady=15, padx=40, fill="both", expand=True)

    tk.Label(frame_login, text="Usuario:", font=("Arial", 10)).pack(anchor="w", pady=(10, 5))
    entry_usuario = tk.Entry(frame_login, font=("Arial", 11), width=25)
    entry_usuario.pack(pady=(0, 15))
    entry_usuario.focus()

    tk.Label(frame_login, text="Contraseña:", font=("Arial", 10)).pack(anchor="w", pady=5)
    entry_contrasena = tk.Entry(frame_login, font=("Arial", 11), width=25, show="●")
    entry_contrasena.pack(pady=(0, 12))
    entry_contrasena.bind("<Return>", validar_login)

    tk.Button(frame_login, text="🔐 Iniciar Sesión", command=validar_login,
              bg="#4CAF50", fg="white", font=("Arial", 10, "bold")).pack(pady=6, fill="x", ipady=8)

    tk.Label(login_window, text="Usuarios de prueba: admin / gerente / empleado",
             font=("Arial", 8), fg="#999").pack()

    login_window.mainloop()


# ==========================================
# PUNTO DE ENTRADA
# ==========================================
if __name__ == "__main__":
    mostrar_login()
