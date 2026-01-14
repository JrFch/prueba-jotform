from flask import Flask, request, jsonify
from azure.cosmos import CosmosClient
import os
import time
import json  # <--- IMPORTANTE: Agrega esto para leer la "caja" de JotForm

app = Flask(__name__)

# Configuración (Render inyectará estos valores)
ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
KEY = os.environ.get("COSMOS_KEY")
DATABASE_NAME = "ley_datos_personales_fch"
CONTAINER_NAME = "Pruebas"

@app.route('/')
def home():
    return "¡Servidor de Prueba Activo!"

@app.route('/webhook', methods=['POST'])
def recibir_datos():
    try:
        # 1. Recibimos el paquete de JotForm
        datos_form = request.form.to_dict()
        print(f"📦 Paquete recibido. Intentando abrir rawRequest...")

        # 2. ABRIMOS LA CAJA (rawRequest)
        # JotForm mete todo el JSON real dentro de este campo string
        if 'rawRequest' in datos_form:
            datos = json.loads(datos_form['rawRequest'])
        else:
            datos = datos_form # Fallback por si acaso

        print(f"📩 Datos desempaquetados: {datos}")

        # 3. Extraemos los datos usando los nombres que vimos en TU log
        # Nota: JotForm a veces agrega prefijos como 'q5_' o 'q6_'
        
        # NOMBRE: Viene separado en 'first' y 'last' dentro de 'q5_nombre'
        nombre_obj = datos.get('q5_nombre', {})
        if isinstance(nombre_obj, dict):
            nombre_real = f"{nombre_obj.get('first', '')} {nombre_obj.get('last', '')}".strip()
        else:
            nombre_real = "Sin Nombre"

        # EMAIL: Viene en 'q6_email'
        email_real = datos.get('q6_email')

        # FOTO: Viene como una LISTA dentro de 'foto' (sin q_prefix en este caso)
        fotos_lista = datos.get('foto', [])
        foto_url_real = fotos_lista[0] if fotos_lista else None


        # 4. Guardar en Azure
        if ENDPOINT and KEY:
            client = CosmosClient(ENDPOINT, KEY)
            database = client.get_database_client(DATABASE_NAME)
            container = database.get_container_client(CONTAINER_NAME)

            nuevo_registro = {
                "id": f"test-{int(time.time())}",
                "nombre": nombre_real,
                "email": email_real,
                "foto_url": foto_url_real,
                "origen": "JotForm Render Test",
                "_ts": int(time.time())
            }

            container.upsert_item(nuevo_registro)
            print(f"✅ Guardado: {nombre_real} - {email_real}")
            return jsonify({"success": True, "msg": "Guardado correctamente"}), 200
        else:
            print("❌ Error: Faltan credenciales de Azure")
            return jsonify({"error": "Faltan credenciales"}), 500

    except Exception as e:
        print(f"❌ Error crítico: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
