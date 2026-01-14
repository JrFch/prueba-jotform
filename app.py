from flask import Flask, request, jsonify, render_template, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from functools import wraps
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import secrets

from config import Config
from excel_processor import excel_processor
from cosmos_client import cosmos_manager
from consent_processor import consent_processor
from arco_processor import arco_processor
from reportes import generar_reporte_tendencia
from flask import send_file
from pdf_generator import generar_pdf_consentimiento 
from werkzeug.security import generate_password_hash, check_password_hash
import uuid


fecha_chile = datetime.now(ZoneInfo('America/Santiago')).date()

# Inicializar Flask
app = Flask(
    __name__,
    template_folder='../frontend/templates',
    static_folder='../frontend/static'
)
app.config.from_object(Config)

# Configuración de sesiones
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hora
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False  # True solo en HTTPS

# Habilitar CORS con soporte para credenciales
CORS(app, supports_credentials=True, origins=['http://localhost:4200'])

# ==================== CREDENCIALES (temporal - mejorar con BD) ====================
USUARIOS_VALIDOS = {
    'fspierccolli@fch.cl': '123456'
}

# ==================== DECORADOR DE AUTENTICACIÓN ====================
def login_required(f):
    """Decorador para verificar autenticación en rutas"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        
        if 'email' not in session: 
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ==================== RUTAS DE AUTENTICACIÓN ====================

@app.route('/login')
def login():
    """Página de login"""
    
    if 'email' in session:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Cerrar sesión"""
    session.clear()
    return redirect(url_for('login'))


# ==================== RUTAS DE PÁGINAS ====================

@app.route('/')
@login_required
def index():
    """Página principal"""
    return render_template('index.html')


@app.route('/modulo/rat')
@login_required
def modulo_rat():
    """Página del módulo RAT"""
    return render_template('rat.html')


@app.route('/modulo/consentimientos')
@login_required
def modulo_consentimientos():
    """Página del módulo de Consentimientos"""
    return render_template('consentimientos.html')


@app.route('/modulo/solicitudes')
@login_required
def modulo_solicitudes():
    """Página del módulo de Solicitudes"""
    return render_template('solicitudes.html')


@app.route('/modulo/reportes')
@login_required
def modulo_reportes():
    """Página del módulo de Reportes"""
    return render_template('reportes.html')


@app.route('/modulo/administracion')
@login_required
def modulo_administracion():
    """Página del módulo de Administración"""
    return render_template('administracion.html')


# ==================== API ENDPOINTS DE AUTENTICACIÓN ====================

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.json
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')

    try:
        
        container = cosmos_manager.client.get_database_client(Config.COSMOS_DATABASE).get_container_client('Usuarios')

        
        query = "SELECT * FROM c WHERE c.email = @email"
        params = [{"name": "@email", "value": email}]
        
        items = list(container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=False 
        ))

        
        if not items:
            return jsonify({"success": False, "error": "Credenciales inválidas"}), 401

        usuario_db = items[0]

        
        if usuario_db.get('estado') != 'activo':
            return jsonify({"success": False, "error": "Usuario inactivo. Contacte al administrador."}), 403

        
        if check_password_hash(usuario_db['password_hash'], password):
            
            
            session['user_id'] = usuario_db['id']
            session['email'] = usuario_db['email']
            session['rol'] = usuario_db.get('rol', 'user')
            session['nombre'] = usuario_db.get('nombre', 'Usuario')
            
            
            try:
                registrar_log("LOGIN", f"Ingreso exitoso", usuario_db['email'])
            except: pass

            return jsonify({
                "success": True, 
                "redirect": "/",
                "user": {"nombre": usuario_db['nombre'], "rol": session['rol']}
            }), 200
        else:
            return jsonify({"success": False, "error": "Credenciales inválidas"}), 401

    except Exception as e:
        print(f"Error en login: {e}")
        return jsonify({"success": False, "error": "Error de sistema"}), 500


@app.route('/api/auth/verificar-sesion', methods=['GET'])
def verificar_sesion():
    """Verificar si existe una sesión activa"""
    
    if 'email' in session:
        return jsonify({
            "autenticado": True,
            "email": session.get('email'),
            "nombre": session.get('nombre'), 
            "rol": session.get('rol')
        }), 200
    else:
        return jsonify({
            "autenticado": False
        }), 200

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """Cerrar sesión vía API"""
    session.clear()
    return jsonify({
        "success": True,
        "message": "Sesión cerrada exitosamente"
    }), 200


# ==================== API ENDPOINTS ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Endpoint de verificación de salud del sistema"""
    cosmos_status = cosmos_manager.is_connected()
    return jsonify({
        "status": "ok",
        "cosmos_db": "conectado" if cosmos_status else "desconectado",
        "version": "1.0.0"
    })


@app.route('/api/rat/upload', methods=['POST'])
def upload_excel():
    """
    Endpoint para cargar y procesar archivos Excel

    Retorna los datos del archivo procesado para revisión
    """
    if 'file' not in request.files:
        return jsonify({
            "success": False,
            "error": "No se encontró el archivo en la solicitud"
        }), 400

    file = request.files['file']

    resultado = excel_processor.procesar_archivo(file)

    if resultado["success"]:
        return jsonify(resultado), 200
    else:
        return jsonify(resultado), 400


@app.route('/api/rat/guardar', methods=['POST'])
def guardar_rat():
    """
    Endpoint para guardar los datos RAT en Cosmos DB

    Espera un JSON con los datos editados del RAT
    """
    try:
        datos = request.get_json()

        if not datos:
            return jsonify({
                "success": False,
                "error": "No se recibieron datos para guardar"
            }), 400

        # Intentar inicializar Cosmos DB si no está conectado
        if not cosmos_manager.is_connected():
            cosmos_manager.initialize()

        # Guardar en Cosmos DB
        resultado = cosmos_manager.guardar_registro_rat(datos)

        if resultado["success"]:
            return jsonify(resultado), 201
        else:
            return jsonify(resultado), 500

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al procesar la solicitud: {str(e)}"
        }), 500


@app.route('/api/rat/listar', methods=['GET'])
def listar_rat():
    """
    Endpoint para listar los registros RAT almacenados
    """
    limite = request.args.get('limite', 100, type=int)
    resultado = cosmos_manager.obtener_registros_rat(limite)

    if resultado["success"]:
        return jsonify(resultado), 200
    else:
        return jsonify(resultado), 500


@app.route('/api/rat/<registro_id>', methods=['GET'])
def obtener_rat(registro_id):
    """
    Endpoint para obtener un registro RAT específico
    """
    resultado = cosmos_manager.obtener_registro_por_id(registro_id)

    if resultado["success"]:
        return jsonify(resultado), 200
    else:
        status_code = 404 if "no encontrado" in resultado.get("error", "").lower() else 500
        return jsonify(resultado), status_code


@app.route('/api/cosmos/status', methods=['GET'])
def cosmos_status():
    """
    Endpoint para verificar el estado de conexión con Cosmos DB
    """
    conectado = cosmos_manager.is_connected()

    if not conectado:
        # Intentar reconectar
        conectado = cosmos_manager.initialize()

    return jsonify({
        "connected": conectado,
        "conectado": conectado,
        "database": Config.COSMOS_DATABASE if conectado else None,
        "base_datos": Config.COSMOS_DATABASE if conectado else None,
        "container": Config.COSMOS_CONTAINER if conectado else None,
        "contenedor": Config.COSMOS_CONTAINER if conectado else None
    })


# ==================== API ENDPOINTS CONSENTIMIENTOS ====================

@app.route('/api/consentimientos/guardar', methods=['POST'])
def guardar_consentimiento():
    """
    Endpoint para guardar un nuevo consentimiento
    """
    try:
        datos = request.get_json()

        if not datos:
            return jsonify({
                "success": False,
                "error": "No se recibieron datos"
            }), 400

        # Validar datos
        validacion = consent_processor.validar_consentimiento(datos)
        if not validacion['valid']:
            return jsonify({
                "success": False,
                "error": "Datos inválidos",
                "detalles": validacion['errors']
            }), 400

        # Preparar consentimiento
        consentimiento = consent_processor.preparar_consentimiento(datos)

        # Intentar guardar en Cosmos DB
        if cosmos_manager.is_connected():
            resultado = cosmos_manager.guardar_consentimiento(consentimiento)

            if resultado["success"]:
                return jsonify({
                    "success": True,
                    "message": "Consentimiento guardado exitosamente",
                    "id": consentimiento['id']
                }), 201
            else:
                return jsonify({
                    "success": False,
                    "error": resultado.get("error", "Error al guardar en Cosmos DB")
                }), 500
        else:
            # Modo local (sin Cosmos DB)
            return jsonify({
                "success": True,
                "message": "Consentimiento procesado (modo local - no almacenado)",
                "id": consentimiento['id'],
                "warning": "Base de datos no configurada"
            }), 201

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al procesar la solicitud: {str(e)}"
        }), 500


@app.route('/api/consentimientos/listar', methods=['GET'])
def listar_consentimientos():
    """
    Endpoint para listar consentimientos
    """
    try:
        limite = request.args.get('limite', 100, type=int)

        # Obtener filtros opcionales
        filtros = {
            'estado': request.args.get('estado'),
            'finalidad': request.args.get('finalidad'),
            'busqueda': request.args.get('busqueda')
        }

        # Limpiar filtros vacíos
        filtros = {k: v for k, v in filtros.items() if v}

        if cosmos_manager.is_connected():
            resultado = cosmos_manager.listar_consentimientos(limite)

            if resultado["success"]:
                consentimientos = resultado.get("consentimientos", [])

                # Aplicar filtros si existen
                if filtros:
                    consentimientos = consent_processor.filtrar_consentimientos(
                        consentimientos,
                        filtros
                    )

                # Actualizar estados vencidos
                actualizados = consent_processor.actualizar_estados_vencidos(consentimientos)

                # Guardar estados actualizados en Cosmos DB
                for c in actualizados:
                    cosmos_manager.actualizar_consentimiento(c)

                return jsonify({
                    "success": True,
                    "consentimientos": consentimientos,
                    "total": len(consentimientos)
                }), 200
            else:
                return jsonify(resultado), 500
        else:
            return jsonify({
                "success": True,
                "consentimientos": [],
                "total": 0,
                "warning": "Base de datos no configurada"
            }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al listar consentimientos: {str(e)}"
        }), 500


@app.route('/api/consentimientos/<consentimiento_id>', methods=['GET'])
def obtener_consentimiento(consentimiento_id):
    """
    Endpoint para obtener un consentimiento específico
    """
    try:
        if cosmos_manager.is_connected():
            resultado = cosmos_manager.obtener_consentimiento_por_id(consentimiento_id)

            if resultado["success"]:
                return jsonify(resultado), 200
            else:
                status_code = 404 if "no encontrado" in resultado.get("error", "").lower() else 500
                return jsonify(resultado), status_code
        else:
            return jsonify({
                "success": False,
                "error": "Base de datos no configurada"
            }), 503

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al obtener consentimiento: {str(e)}"
        }), 500
    



@app.route('/api/consentimientos/<consentimiento_id>/editar', methods=['PUT'])
def editar_consentimiento(consentimiento_id):
    """
    Endpoint para editar un consentimiento existente completo
    """
    try:
        datos = request.get_json()
        
        # 1. Verificar conexión
        if not cosmos_manager.is_connected():
            return jsonify({
                "success": False, 
                "error": "Base de datos no configurada"
            }), 503

        # 2. Verificar si existe el registro
        resultado_get = cosmos_manager.obtener_consentimiento_por_id(consentimiento_id)
        if not resultado_get["success"]:
            return jsonify(resultado_get), 404
            
        consentimiento_actual = resultado_get["consentimiento"]
        
        # 3. Validar que esté activo (opcional, pero recomendado)
        if consentimiento_actual.get('estado') != 'activo':
             return jsonify({
                "success": False, 
                "error": "No se puede editar un consentimiento que no está activo"
            }), 400

        # 4. Actualizar campos permitidos (manteniendo ID y metadatos originales)
        # Actualizamos datos del titular
        if 'titular' in datos:
            if 'titular' not in consentimiento_actual:
                consentimiento_actual['titular'] = {}
            consentimiento_actual['titular'].update(datos['titular'])
            
        # Lista de campos que se pueden modificar directamente
        campos_editables = [
            'finalidad', 'finalidad_detalle', 'tipo_datos', 'modo_obtencion', 
            'fecha_otorgamiento', 'plazo_conservacion', 'fecha_vencimiento', 
            'transferencia_internacional', 'paises_destino', 'observaciones'
        ]
        
        for campo in campos_editables:
            if campo in datos:
                consentimiento_actual[campo] = datos[campo]
                
        
        fecha_actual = datetime.utcnow().isoformat()
        consentimiento_actual['fecha_actualizacion'] = fecha_actual
        
        
        if 'historial' not in consentimiento_actual:
            consentimiento_actual['historial'] = []
            
        consentimiento_actual['historial'].append({
            "accion": "edicion",
            "fecha": fecha_actual,
            "estado": consentimiento_actual['estado'],
            "observacion": "Edición de datos del consentimiento"
        })
        
        
        resultado_save = cosmos_manager.actualizar_consentimiento(consentimiento_actual)
        
        if resultado_save["success"]:
            return jsonify({
                "success": True, 
                "message": "Consentimiento editado correctamente",
                "id": consentimiento_id
            }), 200
        else:
            return jsonify(resultado_save), 500

    except Exception as e:
        return jsonify({
            "success": False, 
            "error": f"Error al editar: {str(e)}"
        }), 500
@app.route('/api/consentimientos/<consentimiento_id>/revocar', methods=['POST'])
def revocar_consentimiento(consentimiento_id):
    """
    Endpoint para revocar un consentimiento
    """
    try:
        datos = request.get_json()
        motivo = datos.get('motivo', '').strip()

        if not motivo:
            return jsonify({
                "success": False,
                "error": "El motivo de revocación es obligatorio"
            }), 400

        if cosmos_manager.is_connected():
            # Obtener consentimiento actual
            resultado = cosmos_manager.obtener_consentimiento_por_id(consentimiento_id)

            if not resultado["success"]:
                return jsonify(resultado), 404

            consentimiento = resultado["consentimiento"]

            # Verificar que esté activo
            if consentimiento['estado'] != 'activo':
                return jsonify({
                    "success": False,
                    "error": "Solo se pueden revocar consentimientos activos"
                }), 400

            # Revocar consentimiento
            consentimiento_revocado = consent_processor.revocar_consentimiento(
                consentimiento,
                motivo
            )

            # Actualizar en Cosmos DB
            resultado_update = cosmos_manager.actualizar_consentimiento(consentimiento_revocado)

            if resultado_update["success"]:
                return jsonify({
                    "success": True,
                    "message": "Consentimiento revocado exitosamente"
                }), 200
            else:
                return jsonify(resultado_update), 500
        else:
            return jsonify({
                "success": False,
                "error": "Base de datos no configurada"
            }), 503

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al revocar consentimiento: {str(e)}"
        }), 500


@app.route('/api/consentimientos/metricas', methods=['GET'])
def obtener_metricas_consentimientos():
    """
    Endpoint para obtener métricas de consentimientos
    """
    try:
        if cosmos_manager.is_connected():
            resultado = cosmos_manager.listar_consentimientos(1000)

            if resultado["success"]:
                consentimientos = resultado.get("consentimientos", [])
                metricas = consent_processor.obtener_metricas(consentimientos)

                return jsonify({
                    "success": True,
                    "metricas": metricas
                }), 200
            else:
                return jsonify(resultado), 500
        else:
            return jsonify({
                "success": True,
                "metricas": {
                    "total": 0,
                    "activos": 0,
                    "revocados": 0,
                    "expirados": 0
                },
                "warning": "Base de datos no configurada"
            }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al obtener métricas: {str(e)}"
        }), 500
    
@app.route('/api/consentimientos/<consentimiento_id>/certificado', methods=['GET'])
def descargar_certificado(consentimiento_id):
    """
    Endpoint para generar un pdf del consentimiento
    """
    try:
        resultado = cosmos_manager.obtener_consentimiento_por_id(consentimiento_id)
        if not resultado["success"]:
            return jsonify(resultado), 404

        consentimiento = resultado["consentimiento"]

        
        if 'fecha_otorgamiento' in consentimiento and consentimiento['fecha_otorgamiento']:
            try:
                
                fecha_obj = datetime.strptime(consentimiento['fecha_otorgamiento'], '%Y-%m-%d')
                
                consentimiento['fecha_otorgamiento'] = fecha_obj.strftime('%d-%m-%Y')
            except ValueError:
                
                pass
        # ---------------------------
        
        pdf_buffer = generar_pdf_consentimiento(consentimiento)
        
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=f"Certificado_{consentimiento['titular']['rut']}.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        print(f"Error PDF: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    



# ==================== API ENDPOINTS SOLICITUDES ARCO ====================

@app.route('/api/solicitudes/listar', methods=['GET'])
def listar_solicitudes():
    try:
        
        fecha_desde = request.args.get('fecha_desde')
        fecha_hasta = request.args.get('fecha_hasta')
        tipo_derecho = request.args.get('tipo')
        estado = request.args.get('estado')
        busqueda = request.args.get('busqueda')
        limite = request.args.get('limite', 100, type=int)

        
        if not arco_processor.is_connected():
            arco_processor.initialize()
            
        
        sql_query = "SELECT * FROM c WHERE 1=1"
        parametros = []

       
        if fecha_desde and fecha_hasta:
            try:
                
                ts_inicio = datetime.strptime(fecha_desde, '%Y-%m-%d').timestamp()
                
                
                dt_fin = datetime.strptime(fecha_hasta, '%Y-%m-%d')
                ts_fin = dt_fin.replace(hour=23, minute=59, second=59).timestamp()

                sql_query += " AND (c._ts >= @ts_inicio AND c._ts <= @ts_fin)"
                parametros.append({"name": "@ts_inicio", "value": ts_inicio})
                parametros.append({"name": "@ts_fin", "value": ts_fin})
            except Exception as e:
                print(f"Error procesando fechas: {e}")

        
        if tipo_derecho:
        
            sql_query += " AND LOWER(c.tipo_solicitud) = @tipo"
            parametros.append({"name": "@tipo", "value": tipo_derecho.lower()})


        if estado and estado != 'todos':
            print(f"✅ Aplicando filtro de estado inteligente: {estado}")
            
           
            
            sql_query += " AND (IS_DEFINED(c.estado_sistema) ? c.estado_sistema : c.estado) = @estado"
            parametros.append({"name": "@estado", "value": estado})

        # --- FILTRO DE BÚSQUEDA (Nombre o RUT) ---
        if busqueda:
            busqueda = busqueda.lower().strip()
            
            sql_query += """ AND (
                CONTAINS(LOWER(c.titular.rut), @busqueda) OR 
                CONTAINS(LOWER(c.titular.nombre), @busqueda)
            )"""
            parametros.append({"name": "@busqueda", "value": busqueda})

        
        sql_query += " ORDER BY c._ts DESC"

        
        items = list(arco_processor.container.query_items(
            query=sql_query,
            parameters=parametros,
            enable_cross_partition_query=True,
            max_item_count=limite
        ))

        return jsonify({"success": True, "solicitudes": items}), 200

    except Exception as e:
        print(f"Error crítico en listar solicitudes: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    
@app.route('/api/solicitudes/<solicitud_id>', methods=['GET'])
def obtener_solicitud(solicitud_id):
    """
    Endpoint para obtener una solicitud ARCO específica por ID
    Contenedor: ARCO_titulares_webFch
    """
    try:
        resultado = arco_processor.obtener_solicitud_por_id(solicitud_id)

        if resultado["success"]:
            return jsonify(resultado), 200
        else:
            status_code = 404 if "no encontrado" in resultado.get("error", "").lower() else 500
            return jsonify(resultado), status_code

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al obtener solicitud: {str(e)}"
        }), 500


@app.route('/api/solicitudes/nuevas', methods=['GET'])
def obtener_nuevas_solicitudes():
    """
    Endpoint para obtener solicitudes ARCO nuevas (últimos 15 minutos por defecto)
    Contenedor: ARCO_titulares_webFch
    """
    try:
        minutos = request.args.get('minutos', 15, type=int)
        resultado = arco_processor.obtener_nuevas_solicitudes(minutos)

        if resultado["success"]:
            return jsonify(resultado), 200
        else:
            return jsonify(resultado), 500

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al obtener nuevas solicitudes: {str(e)}"
        }), 500


@app.route('/api/solicitudes/metricas', methods=['GET'])
def obtener_metricas_solicitudes():
    """
    Endpoint para obtener métricas de solicitudes ARCO
    Contenedor: ARCO_titulares_webFch
    """
    try:
        resultado = arco_processor.calcular_metricas()

        if resultado["success"]:
            return jsonify(resultado), 200
        else:
            return jsonify(resultado), 500

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al obtener métricas: {str(e)}"
        }), 500


@app.route('/api/solicitudes/<solicitud_id>/cambiar-estado', methods=['POST'])
def cambiar_estado_solicitud(solicitud_id):
    """
    Endpoint para cambiar el estado_sistema de una solicitud ARCO
    Estados válidos: abierto, cerrado, prorroga
    """
    try:
        datos = request.get_json()

        if not datos:
            return jsonify({
                "success": False,
                "error": "No se recibieron datos"
            }), 400

        nuevo_estado = datos.get('estado_sistema')
        observaciones = datos.get('observaciones', '')

        # Validar que el estado sea válido
        estados_validos = ['abierto', 'cerrado', 'prorroga']
        if nuevo_estado not in estados_validos:
            return jsonify({
                "success": False,
                "error": f"Estado inválido. Debe ser uno de: {', '.join(estados_validos)}"
            }), 400

        # Actualizar el estado en Cosmos DB
        resultado = arco_processor.cambiar_estado_solicitud(
            solicitud_id=solicitud_id,
            nuevo_estado=nuevo_estado,
            observaciones=observaciones
        )

        if resultado["success"]:
            return jsonify(resultado), 200
        else:
            status_code = 404 if "no encontrado" in resultado.get("error", "").lower() else 500
            return jsonify(resultado), status_code

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Error al cambiar estado: {str(e)}"
        }), 500


# ==================== API ENDPOINTS REPORTES ====================

@app.route('/api/reportes/datos-tendencias', methods=['GET'])
def obtener_datos_tendencias():
    """
    Endpoint para obtener datos de tendencias para gráficos
    """
    try:
        if not cosmos_manager.is_connected():
            return jsonify({
                "success": False,
                "error": "Base de datos no configurada"
            }), 503

        # Obtener consentimientos con todos los campos
        query_consentimientos = """
            SELECT * FROM c
            WHERE c.tipo_documento = 'consentimiento'
            ORDER BY c.fecha_registro DESC
        """
        consentimientos = list(cosmos_manager.container.query_items(
            query=query_consentimientos,
            enable_cross_partition_query=True
        ))

        # Obtener solicitudes ARCO desde el contenedor especializado
        resultado_solicitudes = arco_processor.listar_todas_solicitudes(limite=500)

        if not resultado_solicitudes.get("success"):
            raise Exception(resultado_solicitudes.get("error", "No se pudieron obtener las solicitudes ARCO"))

        solicitudes = resultado_solicitudes.get("solicitudes", [])

        return jsonify({
            "success": True,
            "consentimientos": consentimientos,
            "solicitudes": solicitudes
        }), 200

    except Exception as e:
        print(f"ERROR obteniendo datos de tendencias: {e}")
        return jsonify({
            "success": False,
            "error": f"Error al obtener datos: {str(e)}"
        }), 500


@app.route('/api/reportes/metricas-generales', methods=['GET'])
def obtener_metricas_generales():
    """
    Endpoint para obtener métricas generales del sistema
    """
    try:
        if not cosmos_manager.is_connected():
            return jsonify({
                "success": False,
                "error": "Base de datos no configurada"
            }), 503

        # Métricas de consentimientos
        resultado_consentimientos = cosmos_manager.listar_consentimientos(1000)
        consentimientos = resultado_consentimientos.get("consentimientos", []) if resultado_consentimientos["success"] else []

        metricas_consentimientos = consent_processor.obtener_metricas(consentimientos)

        # Métricas de solicitudes ARCO desde Cosmos
        resultado_solicitudes = arco_processor.calcular_metricas()
        if not resultado_solicitudes.get("success"):
            raise Exception(resultado_solicitudes.get("error", "No se pudieron obtener las métricas de solicitudes ARCO"))
        metricas_solicitudes = resultado_solicitudes.get("metricas", {})

        return jsonify({
            "success": True,
            "metricas": {
                "consentimientos": metricas_consentimientos,
                "solicitudes": metricas_solicitudes
            }
        }), 200

    except Exception as e:
        print(f"ERROR obteniendo métricas generales: {e}")
        return jsonify({
            "success": False,
            "error": f"Error al obtener métricas: {str(e)}"
        }), 500


@app.route('/api/reportes/consentimiento/<consentimiento_id>', methods=['PUT'])
def actualizar_consentimiento(consentimiento_id):
    """
    Endpoint para actualizar un consentimiento
    """
    try:
        if not cosmos_manager.is_connected():
            return jsonify({
                "success": False,
                "error": "Base de datos no configurada"
            }), 503

        # Obtener datos del request
        datos = request.get_json()

        if not datos:
            return jsonify({
                "success": False,
                "error": "No se recibieron datos para actualizar"
            }), 400

        # Obtener el consentimiento actual
        query = f"SELECT * FROM c WHERE c.id = '{consentimiento_id}' AND c.tipo_documento = 'consentimiento'"
        resultados = list(cosmos_manager.container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))

        if not resultados:
            return jsonify({
                "success": False,
                "error": "Consentimiento no encontrado"
            }), 404

        consentimiento = resultados[0]

        # Actualizar campos
        if 'titular' in datos:
            if 'titular' not in consentimiento:
                consentimiento['titular'] = {}
            consentimiento['titular'].update(datos['titular'])

        if 'finalidad' in datos:
            consentimiento['finalidad'] = datos['finalidad']

        if 'estado' in datos:
            estado_anterior = consentimiento.get('estado', 'activo')
            consentimiento['estado'] = datos['estado']

            # Si el estado cambió, agregar al historial
            if estado_anterior != datos['estado']:
                if 'historial' not in consentimiento:
                    consentimiento['historial'] = []

                consentimiento['historial'].append({
                    "accion": "modificacion",
                    "fecha": fecha_chile().isoformat(),
                    "estado": datos['estado'],
                    "observacion": f"Estado cambiado de {estado_anterior} a {datos['estado']}"
                })

        if 'observaciones' in datos:
            consentimiento['observaciones'] = datos['observaciones']

        # Actualizar fecha de actualización
        consentimiento['fecha_actualizacion'] = datetime.utcnow().isoformat()

        # Guardar en Cosmos DB
        cosmos_manager.container.upsert_item(consentimiento)

        return jsonify({
            "success": True,
            "message": "Consentimiento actualizado exitosamente",
            "consentimiento": consentimiento
        }), 200

    except Exception as e:
        print(f"ERROR actualizando consentimiento: {e}")
        return jsonify({
            "success": False,
            "error": f"Error al actualizar consentimiento: {str(e)}"
        }), 500


@app.route('/api/reportes/consentimiento/<consentimiento_id>', methods=['DELETE'])
def eliminar_consentimiento(consentimiento_id):
    """
    Endpoint para eliminar un consentimiento
    """
    try:
        if not cosmos_manager.is_connected():
            return jsonify({
                "success": False,
                "error": "Base de datos no configurada"
            }), 503

        # Obtener el consentimiento para verificar que existe
        query = f"SELECT * FROM c WHERE c.id = '{consentimiento_id}' AND c.tipo_documento = 'consentimiento'"
        resultados = list(cosmos_manager.container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))

        if not resultados:
            return jsonify({
                "success": False,
                "error": "Consentimiento no encontrado"
            }), 404

        consentimiento = resultados[0]

        # Eliminar de Cosmos DB
        cosmos_manager.container.delete_item(
            item=consentimiento_id,
            partition_key='consentimiento'
        )

        return jsonify({
            "success": True,
            "message": "Consentimiento eliminado exitosamente"
        }), 200

    except Exception as e:
        print(f"ERROR eliminando consentimiento: {e}")
        return jsonify({
            "success": False,
            "error": f"Error al eliminar consentimiento: {str(e)}"
        }), 500


# ==================== API ENDPOINTS DE ADMINISTRACIÓN ====================

@app.route('/api/admin/validar-cosmos', methods=['GET'])
@login_required
def validar_cosmos():
    """
    Endpoint para validar la conexión con Cosmos DB
    Retorna información sobre el estado de la conexión y los contenedores
    """
    try:
        # Verificar conexión con cosmos_manager (contenedor principal)
        cosmos_connected = cosmos_manager.is_connected()
        if not cosmos_connected:
            cosmos_connected = cosmos_manager.initialize()

        # Verificar conexión con arco_processor
        arco_connected = arco_processor.is_connected()
        if not arco_connected:
            arco_connected = arco_processor.initialize()

        if not cosmos_connected and not arco_connected:
            return jsonify({
                "success": False,
                "error": "No se pudo establecer conexión con ningún contenedor de Cosmos DB"
            }), 503

        # Obtener información de conexión
        endpoint = os.environ.get('COSMOS_ENDPOINT', 'No configurado')
        database = 'ley_datos_personales_fch'

        resultado = {
            "success": True,
            "endpoint": endpoint,
            "database": database,
            "container_rat": cosmos_manager.container.id if cosmos_connected else 'No conectado',
            "container_arco": arco_processor.container.id if arco_connected else 'No conectado',
            "cosmos_connected": cosmos_connected,
            "arco_connected": arco_connected
        }

        return jsonify(resultado), 200

    except Exception as e:
        print(f"ERROR validando Cosmos DB: {e}")
        return jsonify({
            "success": False,
            "error": f"Error al validar Cosmos DB: {str(e)}"
        }), 500


@app.route('/api/admin/testear-contenedor/<nombre_contenedor>', methods=['GET'])
@login_required
def testear_contenedor(nombre_contenedor):
    """
    Endpoint para testear un contenedor específico
    Retorna el número de registros en el contenedor
    """
    try:
        if nombre_contenedor == 'Registros_titulares_webFch':
            if not cosmos_manager.is_connected():
                cosmos_manager.initialize()

            # Contar registros
            query = "SELECT VALUE COUNT(1) FROM c"
            result = list(cosmos_manager.container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

            count = result[0] if result else 0

            return jsonify({
                "success": True,
                "contenedor": nombre_contenedor,
                "count": count,
                "message": f"Contenedor {nombre_contenedor} validado correctamente"
            }), 200

        elif nombre_contenedor == 'ARCO_titulares_webFch':
            if not arco_processor.is_connected():
                arco_processor.initialize()

            # Contar registros
            query = "SELECT VALUE COUNT(1) FROM c"
            result = list(arco_processor.container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))

            count = result[0] if result else 0

            return jsonify({
                "success": True,
                "contenedor": nombre_contenedor,
                "count": count,
                "message": f"Contenedor {nombre_contenedor} validado correctamente"
            }), 200

        else:
            return jsonify({
                "success": False,
                "error": f"Contenedor {nombre_contenedor} no reconocido"
            }), 400

    except Exception as e:
        print(f"ERROR testeando contenedor {nombre_contenedor}: {e}")
        return jsonify({
            "success": False,
            "error": f"Error al testear contenedor: {str(e)}"
        }), 500


# ==================== SERVIR ARCHIVOS ESTÁTICOS ====================

@app.route('/static/<path:filename>')
def serve_static(filename):
    """Servir archivos estáticos"""
    return send_from_directory(app.static_folder, filename)


# ==================== MANEJO DE ERRORES ====================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Recurso no encontrado"}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Error interno del servidor"}), 500


# ==================== INICIO DE LA APLICACIÓN ====================

if __name__ == '__main__':
    # Crear directorio de uploads si no existe
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

    # Intentar inicializar conexión con Cosmos DB
    print("Inicializando conexión con Azure Cosmos DB...")
    cosmos_manager.initialize()

    # Iniciar servidor
    print("Iniciando servidor Flask...")
    app.run(
        host='0.0.0.0',
        port=5001,
        debug=Config.DEBUG
    )
