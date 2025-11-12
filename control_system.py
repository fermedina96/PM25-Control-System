import requests
import pandas as pd
import numpy as np
import time
import os
import matplotlib.pyplot as plt
from collections import deque
from dotenv import load_dotenv
from datetime import datetime

# =================================================================
# 1. CONFIGURACIÓN Y CARGA DE VARIABLES DE ENTORNO
# =================================================================
load_dotenv() 

# CONFIGURACIÓN DE LA API (AQICN - Ciudad de alta contaminación: DELHI)
CITY_NAME = "Delhi, India" 
LATITUDE = 28.7041 
LONGITUDE = 77.1025 
POLLUTANT = "pm25"

AQICN_API_BASE_URL = "https://api.waqi.info/feed/"
AQICN_API_KEY = os.getenv("AQICN_API_KEY") 

# CONFIGURACIÓN DE WEBHOOK GRATUITO (SLACK)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

# CONFIGURACIÓN DE CONTROL
PM25_ALERT_THRESHOLD = float(os.getenv("PM25_ALERT_THRESHOLD", 100.0))

class AirQualityMonitor:
    
    def __init__(self, buffer_size=10, filter_window=5, consecutive_alerts_required=3):
        
        self.buffer = deque(maxlen=buffer_size) 
        print(f"Monitor inicializado. Buffer máximo: {buffer_size} muestras.")
        self.filter_window = filter_window
        self.current_filtered_value = None
        self.alert_active = False 
        self.consecutive_alerts = 0
        self.consecutive_required = consecutive_alerts_required 
        self.history_raw = []
        self.history_filtered = []
        self.unit = 'μg/m³' 

    # (Funciones de Extracción y Filtro - OMITIDAS, son las mismas)
    # ...
    
    # Esta función debe estar en tu código
    def _fetch_latest_data(self):
        if not AQICN_API_KEY:
            print("❌ ERROR: Falta la AQICN_API_KEY. No se puede conectar al sensor.")
            return None, None, None
        url = f"{AQICN_API_BASE_URL}geo:{LATITUDE};{LONGITUDE}/"
        params = {'token': AQICN_API_KEY}
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data['status'] == 'ok' and 'iaqi' in data['data']:
                pm25_data = data['data']['iaqi'].get(POLLUTANT)
                if pm25_data and 'v' in pm25_data:
                    value = pm25_data['v']
                    timestamp = data['data']['time']['s']
                    unit = 'μg/m³' 
                    print(f"   [API] Conexión OK. Valor extraído: {value:.2f}")
                    return value, timestamp, unit
            print(f"--- Error AQICN: No se encontró el parámetro {POLLUTANT} en los datos de las coordenadas. ---")
            return None, None, None
        except requests.exceptions.RequestException as e:
            print(f"❌ Error de conexión con AQICN: {e}")
            return None, None, None
            
    def update_buffer(self):
        value, timestamp, unit = self._fetch_latest_data()
        if value is not None and value >= 0:
            self.buffer.append(value)
            self.history_raw.append(value)
            self.unit = unit if unit else self.unit
            print(f"--> Dato crudo añadido al buffer ({len(self.buffer)}/{self.buffer.maxlen}): {value:.2f} {self.unit}")
            return True
        else:
            self.history_raw.append(np.nan) 
            print("--- Error: No se pudo obtener un valor válido. Se añade NaN al historial crudo. ---")
            return False

    def apply_filter(self):
        if len(self.buffer) < self.filter_window:
            self.current_filtered_value = None
            self.history_filtered.append(np.nan)
            print(f"   [Filtro] Esperando más datos. Necesitamos {self.filter_window}, tenemos {len(self.buffer)}. Añadiendo NaN.")
            return None

        last_n_data = np.array(list(self.buffer)[-self.filter_window:])
        filtered_value = np.nanmean(last_n_data)
        
        self.current_filtered_value = filtered_value
        self.history_filtered.append(filtered_value)
        print(f"   [FILTRADO] Último valor filtrado (Media de {self.filter_window}): {filtered_value:.2f} {self.unit}")
        return filtered_value

    # =================================================================
    # 4. INTEGRACIÓN IOT Y LÓGICA DE CONTROL (SLACK WEBHOOK GRATUITO)
    # =================================================================
    def _send_webhook_alert(self, value):
        """Envía un mensaje a Slack usando un Webhook (petición HTTP POST)."""
        if not SLACK_WEBHOOK_URL:
            print("--- ALERTA FALLIDA: Falta la URL del Webhook en .env. ---")
            return

        # Payload de Slack para un mensaje con formato profesional
        payload = {
            "text": f":warning: *ALERTA CRÍTICA DE PM2.5 EN {CITY_NAME}*",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🚨 ALARMA DE CONTROL: PM2.5 CRÍTICO ({CITY_NAME})",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Valor Filtrado:*\n{value:.2f} {self.unit}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Umbral de Alerta:*\n{PM25_ALERT_THRESHOLD} {self.unit}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Hora del Disparo:*\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        }
                    ]
                }
            ]
        }

        try:
            # ESTA ES LA PETICIÓN HTTP POST (EL WEBHOOK)
            response = requests.post(SLACK_WEBHOOK_URL, json=payload)
            response.raise_for_status() # Verifica si la petición fue exitosa (código 200)
            print("✅ WEBHOOK: ¡Alerta enviada con éxito a Slack! (Cumple requisito de Webhook/IoT)")
        except requests.exceptions.RequestException as e:
            print(f"❌ Error al enviar Webhook: {e}")
            print("   (Verifique que la URL de Webhook de Slack es correcta y el canal existe).")
    
    def check_and_alert(self):
        """Implementa la lógica de control con Histéresis."""
        filtered_value = self.current_filtered_value
        
        if filtered_value is None or np.isnan(filtered_value) or filtered_value < 0:
            return

        if filtered_value > PM25_ALERT_THRESHOLD:
            self.consecutive_alerts += 1
            print(f"   [ALERTA CHECK] Valor Alto ({filtered_value:.2f}). Consecutivos: {self.consecutive_alerts}/{self.consecutive_required}")

            if self.consecutive_alerts >= self.consecutive_required and not self.alert_active:
                print("🚨🚨 ALARMA ACTIVADA: Disparando Webhook GRATUITO (Slack) 🚨🚨")
                self._send_webhook_alert(filtered_value) # LLAMADA A LA FUNCIÓN CORRECTA
                self.alert_active = True
        
        elif filtered_value < PM25_ALERT_THRESHOLD * 0.9: 
            if self.alert_active:
                print("🟢 ALARMA DESACTIVADA.")
            self.alert_active = False
            self.consecutive_alerts = 0
        
        else:
            if not self.alert_active:
                self.consecutive_alerts = 0

    # (Funciones de Visualización y Main - OMITIDAS, son las mismas)
    # ...
    
    def visualize_analysis(self):
        """Genera el gráfico comparativo Crudo vs. Filtrado."""
        print("\n📊 Generando Gráfico de Análisis...")
        df_analysis = pd.DataFrame({
            'Raw_Data': self.history_raw,
            'Filtered_Data': self.history_filtered
        })
        
        plt.figure(figsize=(12, 6))
        
        plt.plot(df_analysis.index, df_analysis['Raw_Data'], label='Dato Crudo (Con Ruido)', color='gray', alpha=0.6)
        plt.plot(df_analysis.index, df_analysis['Filtered_Data'], label=f'Dato Filtrado (Media Móvil N={self.filter_window})', color='darkblue', linewidth=2)
        plt.axhline(y=PM25_ALERT_THRESHOLD, color='red', linestyle='--', label=f'Umbral de Alerta ({PM25_ALERT_THRESHOLD} {self.unit})')
        
        plt.title(f'Análisis de Señal de {POLLUTANT} en {CITY_NAME}: Control vs. Ruido', fontsize=14)
        plt.xlabel('Iteración / Muestra Temporal', fontsize=12)
        plt.ylabel(f'{POLLUTANT} ({self.unit})', fontsize=12)
        plt.legend()
        plt.grid(True, linestyle=':', alpha=0.7)
        plt.show()

# =================================================================
# 6. EJECUCIÓN DEL SCRIPT
# =================================================================
if __name__ == "__main__":
    
    monitor = AirQualityMonitor(buffer_size=10, filter_window=5, consecutive_alerts_required=3) 
    
    NUM_ITERATIONS = 10 
    for i in range(1, NUM_ITERATIONS + 1):
        print(f"\n================ ITERACIÓN {i}/{NUM_ITERATIONS} ================")
        
        is_successful = monitor.update_buffer() 
        
        if is_successful:
            filtered_val = monitor.apply_filter() 
            
            if filtered_val is not None and not np.isnan(filtered_val):
                monitor.check_and_alert()
        else:
            monitor.history_filtered.append(np.nan)
            print("   [SINCRONIZACIÓN] Extracción fallida. Se añade NaN al historial filtrado para el gráfico.")
            
        time.sleep(2) 

    monitor.visualize_analysis()