import requests # Para realizar peticiones HTTP (API y Webhook).
import pandas as pd # Para el cálculo del filtro digital (Media Móvil).
import numpy as np # Necesario para cálculos numéricos.
import time # Para pausar la ejecución.
import os # Para leer variables de entorno.
import matplotlib.pyplot as plt # Para la visualización y generación de gráficas.
import random # Se mantiene, aunque el movimiento ahora es forzado.
from collections import deque # Buffer de muestras.
from dotenv import load_dotenv # Para cargar el archivo .env.
from datetime import datetime # Para estampar la hora en la alerta.
from tkinter import Tk, messagebox, Button, Label # Librerías para la interfaz gráfica.

# =================================================================
# 0. MAPA DE CIUDADES DISPONIBLES Y UMBRALES ADAPTADOS
# =================================================================
# FUNDAMENTACIÓN: Umbrales adaptados para simular diferentes sensibilidades.
CITY_MAP = {
    "Delhi":    {"lat": 28.7041, "lon": 77.1025, "info": "Alta Contaminación", "threshold": 250.0, "color": "red"},
    "Shanghai": {"lat": 31.2304, "lon": 121.4737, "info": "Contaminación Media", "threshold": 120.0, "color": "orange"},
    "Tokyo":    {"lat": 35.6895, "lon": 139.6917, "info": "Baja Contaminación", "threshold": 70.0, "color": "green"},
    "Paris":    {"lat": 48.8566, "lon": 2.3522, "info": "Baja Contaminación", "threshold": 70.0, "color": "green"},
    "Montevideo": {"lat": -34.9033, "lon": -56.1646, "info": "Baja Contaminación", "threshold": 50.0, "color": "blue"},
}
POLLUTANT = "pm25" 

# Referencias para la DEMOSTRACIÓN DE ALARMA FORZADA
INITIAL_LAT = 35.6895 # TOKYO (Limpio)
INITIAL_LON = 139.6917
GPS_MODE_THRESHOLD = 75.0 

# COORDENADAS DE ALARMA FORZADA (Delhi - Ciudad muy contaminada)
ALARM_LAT = 28.7041 
ALARM_LON = 77.1025 
# =================================================================
# 1. CONFIGURACIÓN Y CARGA DE VARIABLES DE ENTORNO
# =================================================================
load_dotenv() 

AQICN_API_BASE_URL = "https://api.waqi.info/feed/"
AQICN_API_KEY = os.getenv("AQICN_API_KEY") 
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL") 

# Variables globales para el manejo de la UI y el Dashboard
global_selected_mode = None
global_root = None
dashboard_window = None
label_location = None
label_pm25 = None
label_alert_status = None
current_monitor = None
iteration_counter = 0

class AirQualityMonitor:
    
    # -----------------------------------------------------------------
    # FUNDAMENTACIÓN: Inicialización del Lazo de Control
    # -----------------------------------------------------------------
    def __init__(self, buffer_size=10, filter_window=5, consecutive_alerts_required=3, 
                 start_lat=None, start_lon=None, city_key=None):
        
        if city_key and city_key != "GPS_MODE":
            self.city_name = city_key
            self.latitude = CITY_MAP[city_key]["lat"]
            self.longitude = CITY_MAP[city_key]["lon"]
            self.alert_threshold = CITY_MAP[city_key]["threshold"] 
        else: 
            self.city_name = "RUTA GPS DINÁMICA"
            self.latitude = start_lat
            self.longitude = start_lon
            self.alert_threshold = GPS_MODE_THRESHOLD
            
        self.buffer = deque(maxlen=buffer_size) 
        self.filter_window = filter_window 
        self.current_filtered_value = None
        self.alert_active = False 
        self.consecutive_alerts = 0
        self.consecutive_required = consecutive_alerts_required 
        self.history_raw = []
        self.history_filtered = []
        self.unit = 'μg/m³' 
        print(f"Monitor inicializado para {self.city_name}. Umbral: {self.alert_threshold} µg/m³")

    # =================================================================
    # 2. EXTRACCIÓN Y PREPROCESAMIENTO (Input)
    # =================================================================
    def _fetch_latest_data(self):
        """Busca el último valor del sensor AQICN usando la lat/lon actual."""
        if not AQICN_API_KEY:
            print("❌ ERROR: Falta la AQICN_API_KEY.")
            return None, None, None
            
        url = f"{AQICN_API_BASE_URL}geo:{self.latitude};{self.longitude}/" 
        params = {'token': AQICN_API_KEY}
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status() 
            data = response.json()
            
            if data['status'] == 'ok' and 'iaqi' in data['data']:
                pm25_data = data['data']['iaqi'].get(POLLUTANT)
                
                if pm25_data and 'v' in pm25_data and pm25_data['v'] >= 0:
                    value = pm25_data['v']
                    print(f"   [API] Conexión OK. Valor extraído: {value:.2f}")
                    return value, None, self.unit
                
            return None, None, None

        except requests.exceptions.RequestException as e:
            print(f"❌ Error de conexión con AQICN: {e}")
            return None, None, None

    def update_buffer(self):
        """Añade el nuevo dato al buffer."""
        value, _, _ = self._fetch_latest_data()
        
        if value is not None:
            self.buffer.append(value)
            self.history_raw.append(value)
            return True
        else:
            self.history_raw.append(np.nan) 
            return False

    # =================================================================
    # 3. FILTRO DIGITAL (Procesamiento/Control Digital)
    # =================================================================
    def apply_filter(self):
        """Implementa el filtro digital de Media Móvil."""
        if len(self.buffer) < self.filter_window:
            self.current_filtered_value = None
            self.history_filtered.append(np.nan)
            return None

        last_n_data = np.array(list(self.buffer)[-self.filter_window:])
        filtered_value = np.nanmean(last_n_data)
        
        self.current_filtered_value = filtered_value
        self.history_filtered.append(filtered_value)
        print(f"   [FILTRADO] Último valor filtrado: {filtered_value:.2f} {self.unit}")
        return filtered_value

    # =================================================================
    # 4. INTEGRACIÓN IOT Y LÓGICA DE CONTROL (Output y Lógica)
    # =================================================================
    def _send_webhook_alert(self, value):
        """Dispara el actuador IoT: Envía la alerta a Slack."""
        if not SLACK_WEBHOOK_URL:
            print("--- ALERTA FALLIDA: Falta la URL del Webhook en .env. ---")
            return

        payload = {
            "text": f":airplane: *ALERTA EN RUTA: {self.city_name}*",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🚨 ALARMA CRÍTICA: Contaminación PM2.5",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Valor Filtrado Actual:*\n{value:.2f} {self.unit}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Ubicación Actual (Lat/Lon):*\n{self.latitude:.4f} / {self.longitude:.4f}"
                        }
                    ]
                }
            ]
        }

        try:
            response = requests.post(SLACK_WEBHOOK_URL, json=payload)
            response.raise_for_status() 
            print("✅ WEBHOOK: ¡Alerta enviada con éxito a Slack!")
        except requests.exceptions.RequestException as e:
            print(f"❌ Error al enviar Webhook: {e}")
    
    def check_and_alert(self):
        """Lógica de control: Implementa Histéresis."""
        filtered_value = self.current_filtered_value
        
        if filtered_value is None or np.isnan(filtered_value):
            return

        # Lógica ON
        if filtered_value > self.alert_threshold: 
            self.consecutive_alerts += 1
            if self.consecutive_alerts >= self.consecutive_required and not self.alert_active:
                print("🚨🚨 ALARMA ACTIVADA: Disparando Webhook 🚨🚨")
                self._send_webhook_alert(filtered_value) 
                self.alert_active = True
        
        # Lógica OFF: Histéresis (90% del umbral)
        elif filtered_value < self.alert_threshold * 0.9: 
            if self.alert_active:
                print("🟢 ALARMA DESACTIVADA. Valor bajo control.")
            self.alert_active = False
            self.consecutive_alerts = 0
        
        else:
            if not self.alert_active:
                self.consecutive_alerts = 0

    def move_simulated_gps(self, iteration_counter):
        """
        MODIFICACIÓN CLAVE: Simula que la persona salta entre una zona Limpia y una Contaminada 
        cada 10 segundos (cada 2 iteraciones) para demostrar el sistema de alarma.
        """
        # El dashboard se actualiza cada 5s. Un ciclo de 4 iteraciones es 20s.
        # Iteraciones 0 y 1 (0-10s): Polluted (Delhi)
        # Iteraciones 2 y 3 (10-20s): Clean (Tokyo)
        is_alarm_phase = iteration_counter % 4 < 2 

        if is_alarm_phase:
            # FASE 1: Entra a DELHI (Contaminado)
            self.latitude = ALARM_LAT
            self.longitude = ALARM_LON
            status = "Entrando en Zona CRÍTICA (Delhi)"
        else:
            # FASE 2: Vuelve a TOKYO (Limpio)
            self.latitude = INITIAL_LAT
            self.longitude = INITIAL_LON
            status = "Regresando a Zona SEGURA (Tokyo)"

        print(f"   [GPS FORZADO] Coordenadas Actualizadas: Lat {self.latitude:.4f}, Lon {self.longitude:.4f}. Estado: {status}")
            
    # =================================================================
    # 5. VISUALIZACIÓN INICIAL Y ANÁLISIS EXPLORATORIO 
    # =================================================================
    def visualize_analysis(self):
        """Genera el gráfico comparativo (Crudo vs. Filtrado)."""
        if not self.history_raw or len(self.history_raw) < 2:
            print("\n❌ No hay suficientes datos para generar el gráfico.")
            return
            
        print("\n📊 Generando Gráfico de Análisis Exploratorio...")
        
        plt.figure(figsize=(12, 6))
        
        plt.plot(self.history_raw, label='1. Dato Crudo (Sin Filtro/Con Ruido)', color='gray', alpha=0.6)
        plt.plot(self.history_filtered, label=f'2. Dato Filtrado (Media Móvil N={self.filter_window})', color='darkblue', linewidth=2)
        plt.axhline(y=self.alert_threshold, color='red', linestyle='--', label=f'Umbral de Alerta ({self.alert_threshold:.1f} {self.unit})')
        
        plt.title(f'Análisis de Señal de {POLLUTANT} en {self.city_name}: Ruido vs. Control Digital', fontsize=14)
        plt.xlabel('Iteración / Muestra Temporal', fontsize=12)
        plt.ylabel(f'{POLLUTANT} ({self.unit})', fontsize=12)
        plt.legend()
        plt.grid(True, linestyle=':', alpha=0.7)
        plt.show()

# =================================================================
# 6. FUNCIONES DE INTERFAZ GRÁFICA Y DASHBOARD
# =================================================================
def select_city(mode):
    """
    Función llamada por los botones. 
    SOLUCIÓN DE ESTABILIDAD: Usa quit() para detener el mainloop de forma limpia.
    """
    global global_selected_mode
    global_selected_mode = mode
    global_root.quit()

def create_ui_selection():
    """Crea la interfaz inicial con botones de selección."""
    global global_root
    global_root = Tk()
    global_root.title("✈️ PM2.5 Control System: Selecciona el Modo de Monitoreo")
    global_root.geometry("450x450")
    
    Label(global_root, text="Monitor de Riesgo de Contaminación", font=("Arial", 16, "bold")).pack(pady=10)
    Label(global_root, text="Elige el modo de monitoreo para tu viaje:").pack()

    # Opción GPS Dinámico (La opción de demostración clave)
    Button(
        global_root,
        text="🌐 Monitoreo GPS DINÁMICO (DEMO: Alerta Forzada cada 10s)",
        command=lambda: select_city("GPS_MODE"),
        bg="#5a008c", 
        fg="white",
        font=("Arial", 12, "bold"),
        width=40,
        height=2
    ).pack(pady=15)
    
    Label(global_root, text="--- O Ubicación Fija (Umbral Adaptado por Ciudad) ---", font=("Arial", 10)).pack()

    # Botones de ciudades
    for city, data in CITY_MAP.items():
        Button(
            global_root,
            text=f"Monitorear {city} ({data['info']})",
            command=lambda c=city: select_city(c),
            bg=data['color'], 
            fg="white",
            font=("Arial", 9, "bold"),
            width=35,
            height=1
        ).pack(pady=3)
        
    global_root.mainloop()

    # Destrucción Segura: Solo después de que mainloop() ha terminado.
    global_root.destroy() 


def update_dashboard():
    """Ejecuta un ciclo de monitoreo, llama a la alerta de Slack y actualiza la UI."""
    global current_monitor
    global iteration_counter
    global dashboard_window
    
    if current_monitor is None:
        return

    # 1. Ejecutar Lógica de Control
    if global_selected_mode == "GPS_MODE":
        # LLAMADA MODIFICADA para forzar el movimiento cada 10s
        current_monitor.move_simulated_gps(iteration_counter) 
    
    current_monitor.update_buffer() 
    filtered_val = current_monitor.apply_filter() 
        
    if filtered_val is not None and not np.isnan(filtered_val):
        current_monitor.check_and_alert() # Aquí se dispara el Webhook/Alerta a Slack
    
    # 2. Actualizar la Interfaz Gráfica (Dashboard)
    
    # PM2.5
    pm25_text = f"PM2.5 Filtrado: {filtered_val:.2f} {current_monitor.unit}" if filtered_val else "PM2.5 Filtrado: N/A (Esperando datos)"
    label_pm25.config(text=pm25_text, font=("Arial", 16, "bold"))
    
    # Ubicación
    location_text = f"Ubicación: Lat {current_monitor.latitude:.4f}, Lon {current_monitor.longitude:.4f}"
    label_location.config(text=location_text)
    
    # Estado de la Alerta (El color indica la ALARMA)
    if current_monitor.alert_active:
        alert_text = "🚨 ALERTA ACTIVA: ¡Riesgo Alto! (Webhook Enviado)"
        label_alert_status.config(text=alert_text, bg="red", fg="yellow")
    else:
        alert_text = f"🟢 Monitoreo OK (Muestra #{iteration_counter})"
        label_alert_status.config(text=alert_text, bg="green", fg="white")
    
    iteration_counter += 1
    
    # 3. Llamar a esta misma función en 5000 milisegundos (5 segundos)
    dashboard_window.after(5000, update_dashboard)

def start_monitoring_dashboard(monitor_instance, mode):
    """Crea la ventana del dashboard y comienza el bucle de actualización."""
    global dashboard_window
    global label_location, label_pm25, label_alert_status, current_monitor

    current_monitor = monitor_instance
        
    # Inicializar el Dashboard
    dashboard_window = Tk()
    dashboard_window.title(f"📊 Dashboard de Monitoreo - {mode}")
    dashboard_window.geometry("600x450")
    
    # Título
    Label(dashboard_window, text=f"Monitor de Riesgo Contaminación: {mode}", font=("Arial", 18, "bold")).pack(pady=10)
    
    # Ubicación
    Label(dashboard_window, text="--- RASTREO GPS ---", font=("Arial", 12)).pack(pady=5)
    label_location = Label(dashboard_window, text="Lat: ---, Lon: ---", font=("Courier", 12))
    label_location.pack()

    # PM2.5 y Umbral
    Label(dashboard_window, text=f"--- Umbral de Riesgo: {monitor_instance.alert_threshold:.1f} {monitor_instance.unit} ---", font=("Arial", 10)).pack(pady=10)
    label_pm25 = Label(dashboard_window, text="PM2.5 Filtrado: Iniciando...", font=("Arial", 16, "bold"))
    label_pm25.pack(pady=10)
    
    # Estado de la Alerta 
    label_alert_status = Label(dashboard_window, text="Cargando...", font=("Arial", 20, "bold"), fg="white", width=40, height=2)
    label_alert_status.pack(pady=20)

    # Botón para detener y analizar
    Button(dashboard_window, text="Detener y Mostrar Gráfico de Análisis", command=lambda: [current_monitor.visualize_analysis(), dashboard_window.quit()], bg="blue", fg="white").pack(pady=10)
    
    # Inicializar el loop de actualización 
    dashboard_window.after(100, update_dashboard) 
    
    dashboard_window.mainloop()

# =================================================================
# 7. FUNCIÓN MAIN
# =================================================================
if __name__ == "__main__":
    
    # 1. Lanzar la Interfaz de Selección
    create_ui_selection()
    
    if global_selected_mode is None:
        print("--- Ejecución cancelada por el usuario. ---")
        exit()
        
    selected_mode = global_selected_mode
    
    # 2. Inicialización del Backend (Monitor)
    if selected_mode == "GPS_MODE":
        monitor_instance = AirQualityMonitor(
            start_lat=INITIAL_LAT, 
            start_lon=INITIAL_LON, 
            buffer_size=10, 
            filter_window=5, 
            consecutive_alerts_required=3
        )
        mode_label = "DEMO DE ALERTA FORZADA"
    else:
        monitor_instance = AirQualityMonitor(
            city_key=selected_mode, 
            buffer_size=10, 
            filter_window=5, 
            consecutive_alerts_required=3
        )
        mode_label = selected_mode
    
    # 3. Iniciar el Dashboard (Frontend)
    start_monitoring_dashboard(monitor_instance, mode_label)
    
    print("--- SIMULACIÓN FINALIZADA. ---")