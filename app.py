from flask import Flask, request, jsonify
from azure.cosmos import CosmosClient
import os
import time

app = Flask(__name__)


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
        
        datos = request.form.to_dict()
        print(f"📩 Recibido: {datos}")

        
        if ENDPOINT and KEY:
            client = CosmosClient(ENDPOINT, KEY)
            database = client.get_database_client(DATABASE_NAME)
            container = database.get_container_client(CONTAINER_NAME)

            
            nuevo_registro = {
                "id": f"test-{int(time.time())}",
                "nombre": datos.get('nombre'), 
                "email": datos.get('email'),   
                "foto_url": datos.get('foto'),
                "origen": "JotForm Render Test",
                "_ts": int(time.time())
            }

            
            container.upsert_item(nuevo_registro)
            return jsonify({"success": True, "msg": "Guardado en Azure"}), 200
        else:
            return jsonify({"error": "Faltan credenciales de Azure"}), 500

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)