import requests 
import pandas as pd 
import numpy as np 
import time 
import os 
import matplotlib.pyplot as plt 
import random 
from collections import deque 
from dotenv import load_dotenv 
from datetime import datetime 
from tkinter import Tk, messagebox, Button, Label, Frame # Frame para mejor diseño

# =================================================================
# 0. MAPA DE CIUDADES DISPONIBLES Y UMBRALES ADAPTADOS
# =================================================================
POLLUTANT = "pm25" 

# MAPA DE CIUDADES DE RIESGO FIJO
CITY_MAP = {
    "Delhi":    {"lat": 28.7041, "lon": 77.1025, "info": "Alta Contaminación", "threshold": 250.0, "color": "red"},
    "Shanghai": {"lat": 31.2304, "lon": 121.4737, "info": "Contaminación Media", "threshold": 120.0, "color": "orange"},
    "Tokyo":    {"lat": 35.6895, "lon": 139.6917, "info": "Baja Contaminación", "threshold": 70.0, "color": "green"},
    "Paris":    {"lat": 48.8566, "lon": 2.3522, "info": "Baja Contaminación", "threshold": 70.0, "color": "green"},
    "Montevideo": {"lat": -34.9033, "lon": -56.1646, "info": "Baja Contaminación", "threshold": 50.0, "color": "blue"},
}

# LISTA DE PUNTOS GLOBALES PARA LA DEMOSTRACIÓN DE COBERTURA MUNDIAL Y RIESGO
GLOBAL_ROUTE_POINTS = [
    # Zonas de ALTO RIESGO
    {"name": "Lahore, Pakistán", "lat": 31.5497, "lon": 74.3436, "risk": "ALTO", "region": "Asia Central"}, 
    {"name": "Beijing, China", "lat": 39.9042, "lon": 116.4074, "risk": "ALTO", "region": "Asia Oriental"}, 
    {"name": "Santiago, Chile", "lat": -33.4489, "lon": -70.6693, "risk": "ALTO", "region": "Sudamérica"},
    
    # Zonas de BAJO RIESGO
    {"name": "Hobart, Australia", "lat": -42.8821, "lon": 147.3272, "risk": "BAJO", "region": "Oceanía"}, 
    {"name": "Reikiavik, Islandia", "lat": 64.1265, "lon": -21.8174, "risk": "BAJO", "region": "Europa del Norte"}, 
    {"name": "Vancouver, Canadá", "lat": 49.2827, "lon": -123.1207, "risk": "BAJO", "region": "Norteamérica"},
    {"name": "Auckland, NZ", "lat": -36.8485, "lon": 174.7633, "risk": "BAJO", "region": "Oceanía"},
]

# Inicialización en el primer punto.
INITIAL_LAT = GLOBAL_ROUTE_POINTS[0]["lat"] 
INITIAL_LON = GLOBAL_ROUTE_POINTS[0]["lon"]
GPS_MODE_THRESHOLD = 75.0 

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
label_region_info = None 
label_mission_info = None 
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
            self.city_name = "RUTA GPS GLOBAL"
            self.latitude = start_lat
            self.longitude = start_lon
            self.alert_threshold = GPS_MODE_THRESHOLD
            
        self.buffer = deque(maxlen=buffer_size) 
        self.filter_window = filter_window 
        self.consecutive_required = consecutive_alerts_required 
        
        # Variables de estado
        self.current_filtered_value = None
        self.alert_active = False 
        self.consecutive_alerts = 0
        self.history_raw = []
        self.history_filtered = []
        self.unit = 'μg/m³' 
        self.current_location_name = GLOBAL_ROUTE_POINTS[0]["name"]
        self.current_region = GLOBAL_ROUTE_POINTS[0]["region"]
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
                    # Intenta obtener el nombre real de la estación del API para mayor realismo.
                    if 'city' in data['data'] and 'name' in data['data']['city']:
                        self.current_location_name = data['data']['city']['name']
                        if global_selected_mode != "GPS_MODE":
                            self.current_region = "Datos API" 
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
    def apply_filter(self): # <-- ¡Esta es la función que faltaba!
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
            "text": f":airplane: *ALERTA EN RUTA: {self.current_location_name}*",
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
                            "text": f"*Ubicación Actual:*\n{self.current_location_name}, {self.current_region}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Valor Filtrado Actual:*\n{value:.2f} {self.unit}"
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
        Simula un viaje global forzado cada 10 segundos (cada 2 iteraciones)
        para alternar entre zonas de riesgo alto y bajo.
        """
        global GLOBAL_ROUTE_POINTS
        
        # Cambiar de ubicación cada 2 iteraciones (5s * 2 = 10 segundos)
        if iteration_counter % 2 == 0: 
            
            # Rotamos la lista para simular el siguiente punto en el "viaje"
            GLOBAL_ROUTE_POINTS.append(GLOBAL_ROUTE_POINTS.pop(0))
            next_stop = GLOBAL_ROUTE_POINTS[0]

            self.latitude = next_stop["lat"]
            self.longitude = next_stop["lon"]
            self.current_location_name = next_stop["name"]
            self.current_region = next_stop["region"]
            
            status = f"Viajando a: {next_stop['name']}. Riesgo: {next_stop['risk']}"
            print(f"   [GPS GLOBAL] {status}")
        else:
            print(f"   [GPS GLOBAL] Monitoreando {self.current_location_name}...")


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
    """Función llamada por los botones. Usa quit() para detener el mainloop de forma limpia."""
    global global_selected_mode
    global_selected_mode = mode
    global_root.quit()

def create_ui_selection():
    """Crea la interfaz inicial con botones de selección."""
    global global_root
    global_root = Tk()
    global_root.title("✈️ Sistema de Control Digital PM2.5 (Venta Final)")
    global_root.geometry("480x450")
    
    Label(global_root, text="Monitor de Riesgo de Contaminación", font=("Arial", 16, "bold")).pack(pady=10)
    Label(global_root, text="Elige el modo de monitoreo para tu viaje (App Industrial):").pack()

    # Opción GPS Dinámico (La opción de demostración clave)
    Button(
        global_root,
        text="🌎 Monitoreo GLOBAL DINÁMICO (DEMO: Recorrido Forzado)",
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

    # Destrucción Segura
    global_root.destroy() 


def update_dashboard():
    """Ejecuta un ciclo de monitoreo, llama a la alerta de Slack y actualiza la UI profesional."""
    global current_monitor
    global iteration_counter
    global dashboard_window
    
    if current_monitor is None:
        return

    # 1. Ejecutar Lógica de Control
    if global_selected_mode == "GPS_MODE":
        current_monitor.move_simulated_gps(iteration_counter) 
    
    # Obtiene datos del API y actualiza el buffer
    current_monitor.update_buffer() 
    # Aquí está la llamada que fallaba antes.
    filtered_val = current_monitor.apply_filter() 
        
    if filtered_val is not None and not np.isnan(filtered_val):
        current_monitor.check_and_alert() # Aquí se dispara el Webhook/Alerta a Slack
    
    # 2. Actualizar la Interfaz Gráfica (Dashboard)
    
    # UBICACIÓN Y MAPA CONCEPTUAL (Mayor realismo)
    location_text = f"Ciudad/Estación: {current_monitor.current_location_name}"
    region_text = f"Región (Mapa Conceptual): {current_monitor.current_region}"
    label_location.config(text=location_text)
    label_region_info.config(text=region_text)

    # VALOR PM2.5
    pm25_text = f"Valor Filtrado PM2.5: {filtered_val:.2f} {current_monitor.unit}" if filtered_val else "Valor Filtrado PM2.5: N/A (Recopilando datos)"
    label_pm25.config(text=pm25_text)
    
    # INDICADOR DE ESTADO LÓGICO Y ALARMA (El panel principal de estado)
    current_pm25 = filtered_val if filtered_val is not None else 0
    
    if current_monitor.alert_active:
        alert_text = "🚨 ALARMA CRÍTICA: RIESGO ALTO"
        detail_text = "El sistema ha enviado una alerta a Slack (Actuador IoT)."
        bg_color = "red"
        fg_color = "yellow"
    elif current_pm25 > current_monitor.alert_threshold * 0.7:
        alert_text = "⚠️ RIESGO ELEVADO: Cerca del Umbral"
        detail_text = f"El valor {current_pm25:.2f} está en zona de histéresis."
        bg_color = "orange"
        fg_color = "white"
    else:
        alert_text = "🟢 RIESGO BAJO: Monitoreo Controlado"
        detail_text = f"La calidad del aire es aceptable en esta ubicación."
        bg_color = "green"
        fg_color = "white"
        
    label_alert_status.config(text=f"{alert_text}\n{detail_text}", bg=bg_color, fg=fg_color)
    
    iteration_counter += 1
    
    # 3. Llamar a esta misma función en 5000 milisegundos (5 segundos)
    dashboard_window.after(5000, update_dashboard)

def start_monitoring_dashboard(monitor_instance, mode):
    """Crea la ventana del dashboard y comienza el bucle de actualización."""
    global dashboard_window
    global label_location, label_pm25, label_alert_status, label_region_info, label_mission_info, current_monitor, iteration_counter

    current_monitor = monitor_instance
    iteration_counter = 0 
        
    # Inicializar el Dashboard
    dashboard_window = Tk()
    dashboard_window.title(f"📊 Control Digital PM2.5: {mode}")
    dashboard_window.geometry("700x550")
    
    # --- Estructura y Diseño (Frame de Cabecera) ---
    header_frame = Frame(dashboard_window, bg="#2c3e50", padx=10, pady=10) # Fondo oscuro estilo industrial
    header_frame.pack(fill='x')
    
    Label(header_frame, text="SISTEMA DE MONITOREO DE CALIDAD DEL AIRE (Control Digital IoT)", 
          font=("Arial", 18, "bold"), fg="white", bg="#2c3e50").pack()
          
    # --- Panel de Explicación de la Misión ---
    mission_text = (
        "OBJETIVO DE LA MISIÓN: Rastrear partículas PM2.5. "
        f"Umbral de alerta programado: {monitor_instance.alert_threshold:.1f} µg/m³. "
        "El filtro digital (Media Móvil) estabiliza la señal antes de activar el actuador (Slack)."
    )
    label_mission_info = Label(dashboard_window, text=mission_text, font=("Arial", 10), wraplength=650, justify='left', padx=10, pady=10, bg="#ecf0f1")
    label_mission_info.pack(fill='x', pady=5)
    
    # --- Panel de RASTREO (Ubicación) ---
    tracking_frame = Frame(dashboard_window, padx=10, pady=5)
    tracking_frame.pack(fill='x', pady=5)
    
    Label(tracking_frame, text="RASTREO GPS VIVO:", font=("Arial", 12, "underline")).pack(pady=2)
    label_location = Label(tracking_frame, text="Ciudad/Estación: ---", font=("Courier", 13, "bold"))
    label_location.pack(pady=2)
    label_region_info = Label(tracking_frame, text="Región (Mapa Conceptual): ---", font=("Courier", 11))
    label_region_info.pack(pady=2)

    # --- Panel de Datos (PM2.5) ---
    data_frame = Frame(dashboard_window, padx=10, pady=5, bg="#f39c12")
    data_frame.pack(fill='x', pady=5)
    label_pm25 = Label(data_frame, text="Valor Filtrado PM2.5: Iniciando...", font=("Arial", 16, "bold"), fg="white", bg="#f39c12")
    label_pm25.pack(pady=5)
    
    # --- Panel de ALARMA (El panel principal de estado) ---
    label_alert_status = Label(dashboard_window, text="Cargando estado del sistema...", font=("Arial", 20, "bold"), fg="white", width=40, height=3, relief="raised")
    label_alert_status.pack(pady=15, padx=10)

    # Botón para detener y analizar
    Button(dashboard_window, text="Detener Monitoreo y Mostrar Gráfico de Análisis", command=lambda: [current_monitor.visualize_analysis(), dashboard_window.quit()], bg="#3498db", fg="white", font=("Arial", 11)).pack(pady=10)
    
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
            start_lat=GLOBAL_ROUTE_POINTS[0]["lat"], 
            start_lon=GLOBAL_ROUTE_POINTS[0]["lon"], 
            buffer_size=10, 
            filter_window=5, 
            consecutive_alerts_required=3
        )
        mode_label = "RECORRIDO GLOBAL FORZADO"
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