from fastapi import FastAPI, HTTPException, Depends, status, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta
from openpyxl import Workbook
import io
import psycopg2
import os
from dotenv import load_dotenv

# App y CORS
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://desarrollo.webvivefit.online", "https://fitness-dash.celenius.store", "http://localhost:5173","https://frontfitnessdashboard.onrender.com", "https://dashboard-fitness-m.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load env
load_dotenv()
#load_dotenv(dotenv_path="env")
SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT")
    )
from fastapi.security import OAuth2PasswordBearer
from fastapi import Request

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def decode_access_token(token: str) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        user_id = payload.get("user_id")
        roles = payload.get("roles")
        if username is None or user_id is None:
            raise credentials_exception
        return {
            "username": username,
            "user_id": user_id,
            "roles": roles
        }
    except JWTError:
        raise credentials_exception

def get_current_user(token: str = Depends(oauth2_scheme)):
    return decode_access_token(token)

# Los enlaces de descarga (<a href>) no pueden enviar el header Authorization,
# así que los endpoints de exportación a Excel reciben el token por query param.
def get_current_user_desde_query(token: str = Query(...)):
    return decode_access_token(token)

def generar_excel_response(filas: list, columnas: list[tuple[str, str]], nombre_archivo: str) -> StreamingResponse:
    """columnas: lista de (encabezado, clave_en_fila)"""
    wb = Workbook()
    ws = wb.active
    ws.append([encabezado for encabezado, _ in columnas])
    for fila in filas:
        ws.append([fila.get(clave) for _, clave in columnas])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )

@app.get("/asistencias")
def obtener_asistencias(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    query = """
    SELECT
        m.usuario AS monitor_usuario,
        dm.first_name AS monitor_nombre,
        u.usuario AS usuario_asistente,
        du.first_name AS usuario_nombre,
        a.fecha AS fecha_asistencia,
        act.descripcion AS actividad,
        ta.nombre AS tipo_actividad,
        p.nombre AS parque,
        b.nombre AS barrio_actividad,
        c.nombre AS comuna_actividad
    FROM security.users m
    JOIN public.datos_generales dm ON dm."userId" = m.id
    JOIN public.actividade act ON act."userId" = m.id
    JOIN public.tipo_actividad ta ON ta.id = act."tipoActividadId"
    JOIN public.parque p ON p.id = act."parqueId"
    JOIN public.barrio b ON b.id = p."barrioId"
    JOIN public.comuna_corregimiento c ON c.id = b."comunaCorregimientoId"
    JOIN public.asistencia a ON a."actividadId" = act.id
    JOIN public.datos_generales du ON du.document_number = a.documento
    JOIN security.users u ON u.id = du."userId"
    WHERE 'monitor' = ANY(m.role)
        AND 'user' = ANY(u.role)
        AND m.is_active = true
        AND u.is_active = true;
    """
    cur.execute(query)
    columns = [desc[0] for desc in cur.description]
    results = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return results

@app.get("/actividades/mis-actividades")
def obtener_mis_actividades(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Obtener el user_id del token
        user_id = current_user["user_id"]
        
        query = """
        SELECT id, "motivoCancelado", fecha, hora, created_at, updated_at, estado, descripcion, "checkAsistencia", "userId", "parqueId", "tipoActividadId"
        FROM public.actividade
        WHERE "userId" = %s AND estado = true
        """
        
        cur.execute(query, (user_id,))
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        return {
            "actividades": results,
            "total": len(results),
            "usuario_id": user_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener actividades: {str(e)}")
    finally:
        cur.close()
        conn.close()

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = timedelta(minutes=60)):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
class LoginInput(BaseModel):
    username: str
    password: str

@app.post("/login")
def login(input: LoginInput):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, usuario, email, password, role
            FROM security.users
            WHERE usuario = %s OR email = %s
              AND is_active = TRUE
        """, (input.username, input.username))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")

        user_id, usuario, email, hashed_password, roles = row

        if not verify_password(input.password, hashed_password):
            raise HTTPException(status_code=401, detail="Contraseña incorrecta")

        token_data = {
            "sub": usuario,
            "user_id": str(user_id),
            "roles": roles
        }
        token = create_access_token(token_data)

        return {
            "access_token": token,
            "token_type": "bearer",
            "usuario": usuario,
            "roles": roles
        }
    finally:
        cur.close()
        conn.close()

@app.get("/usuarios/count")
def contar_usuarios(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para contar usuarios activos
        cur.execute("""
            SELECT COUNT(*) as total_usuarios
            FROM security.users
            WHERE is_active = TRUE
        """)
        result = cur.fetchone()
        total_usuarios = result[0] if result else 0
        
        return {
            "total_usuarios": total_usuarios,
            "message": f"Total de usuarios activos: {total_usuarios}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al contar usuarios: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/asistencias/count-detalle/{actividad_id}")
def contar_asistencias_con_detalle(actividad_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para obtener detalles de la actividad y contar asistencias
        cur.execute("""
            SELECT 
                act.id,
                act.descripcion,
                act.fecha,
                act.hora,
                act.estado,
                COUNT(a.id) as total_asistencias
            FROM public.actividade act
            LEFT JOIN public.asistencia a ON a."actividadId" = act.id
            WHERE act.id = %s
            GROUP BY act.id, act.descripcion, act.fecha, act.hora, act.estado
        """, (actividad_id,))
        
        result = cur.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Actividad no encontrada")
        
        actividad_id, descripcion, fecha, hora, estado, total_asistencias = result
        
        return {
            "actividad": {
                "id": actividad_id,
                "descripcion": descripcion,
                "fecha": fecha,
                "hora": hora,
                "estado": estado
            },
            "total_asistencias": total_asistencias,
            "message": f"La actividad '{descripcion}' tiene {total_asistencias} asistencias registradas"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener información: {str(e)}")
    finally:
        cur.close()
        conn.close()

#traer parques
@app.get("/parques")
def obtener_parques(
    nombre: str = None, 
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta base para parques activos con información del barrio
        if nombre:
            # Filtrar por nombre (búsqueda parcial, insensible a mayúsculas)
            query = """
            SELECT 
                p.id,
                p.nombre AS parque_nombre,
                p."barrioId",
                b.nombre AS barrio_nombre
            FROM public.parque p
            JOIN public.barrio b ON b.id = p."barrioId"
            WHERE p.estado = true 
              AND LOWER(p.nombre) LIKE LOWER(%s)
            ORDER BY p.nombre ASC
            """
            cur.execute(query, (f"%{nombre}%",))
        else:
            # Traer todos los parques activos
            query = """
            SELECT 
                p.id,
                p.nombre AS parque_nombre,
                p."barrioId",
                b.nombre AS barrio_nombre
            FROM public.parque p
            JOIN public.barrio b ON b.id = p."barrioId"
            WHERE p.estado = true
            ORDER BY p.nombre ASC
            """
            cur.execute(query)
        
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        return {
            "parques": results,
            "total": len(results),
            "filtro_nombre": nombre if nombre else "sin filtro"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener parques: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/asistencias/count-por-parque")
def contar_asistencias_por_parque(
    parque_id: str = None,
    fecha_inicio: str = None,  # Formato: 2025-05-14
    fecha_fin: str = None,     # Formato: 2025-05-14
    fecha_especifica: str = None,  # Formato: 2025-05-14
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para contar asistencias
        base_query = """
        SELECT COUNT(a.id) as total_asistencias
        FROM public.asistencia a
        JOIN public.actividade act ON act.id = a."actividadId"
        JOIN public.parque p ON p.id = act."parqueId"
        WHERE act.estado = true AND p.estado = true
        """
        
        conditions = []
        params = []
        
        # Filtro por parque específico
        if parque_id:
            conditions.append("p.id = %s")
            params.append(parque_id)
        
        # Filtro por fecha específica
        if fecha_especifica:
            conditions.append("a.fecha = %s")
            params.append(fecha_especifica)
        # Filtro por rango de fechas
        elif fecha_inicio and fecha_fin:
            conditions.append("a.fecha BETWEEN %s AND %s")
            params.extend([fecha_inicio, fecha_fin])
        # Solo fecha inicio
        elif fecha_inicio:
            conditions.append("a.fecha >= %s")
            params.append(fecha_inicio)
        # Solo fecha fin
        elif fecha_fin:
            conditions.append("a.fecha <= %s")
            params.append(fecha_fin)
        
        # Añadir condiciones a la consulta
        if conditions:
            base_query += " AND " + " AND ".join(conditions)
        
        cur.execute(base_query, params)
        result = cur.fetchone()
        total_asistencias = result[0] if result else 0
        
        return {
            "total_asistencias": total_asistencias
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al contar asistencias: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/asistencias/por-comuna")
def obtener_asistencias_por_comuna(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        base_query = """
        SELECT
            c.nombre AS comuna_actividad,
            COUNT(a.id) AS total_asistencias
        FROM public.asistencia a
        JOIN public.actividade act ON act.id = a."actividadId"
        JOIN public.parque p ON p.id = act."parqueId"
        JOIN public.barrio b ON b.id = p."barrioId"
        JOIN public.comuna_corregimiento c ON c.id = b."comunaCorregimientoId"
        WHERE act.estado = true
        """

        conditions = []
        params = []

        if fecha_inicio and fecha_fin:
            conditions.append('a.fecha BETWEEN %s AND %s')
            params.extend([fecha_inicio, fecha_fin])
        elif fecha_inicio:
            conditions.append('a.fecha >= %s')
            params.append(fecha_inicio)
        elif fecha_fin:
            conditions.append('a.fecha <= %s')
            params.append(fecha_fin)

        if conditions:
            base_query += " AND " + " AND ".join(conditions)

        base_query += """
        GROUP BY c.nombre
        ORDER BY total_asistencias DESC
        """

        cur.execute(base_query, params)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]

        return {
            "asistencias_por_comuna": results,
            "total_comunas": len(results),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener asistencias por comuna: {str(e)}")
    finally:
        cur.close()
        conn.close()

def _condiciones_fecha(columna: str, fecha_inicio: str, fecha_fin: str):
    conditions = []
    params = []
    if fecha_inicio and fecha_fin:
        conditions.append(f"{columna} BETWEEN %s AND %s")
        params.extend([fecha_inicio, fecha_fin])
    elif fecha_inicio:
        conditions.append(f"{columna} >= %s")
        params.append(fecha_inicio)
    elif fecha_fin:
        conditions.append(f"{columna} <= %s")
        params.append(fecha_fin)
    return conditions, params

@app.get("/reportes/medico")
def reporte_medico(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    formato: str = "json",
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        query = """
        SELECT
            pq.created_at AS fecha_aprobacion,
            dg.first_name AS nombre,
            dg.document_number AS documento,
            dg.phone_number AS telefono,
            dg.address AS direccion,
            dg.gender::text AS sexo,
            dg.birth_date AS fecha_nacimiento,
            pq.observacion AS comentarios_medico,
            pq.aprobado AS aprobado
        FROM public.parq pq
        JOIN public.datos_generales dg ON dg."userId" = pq."userId"
        """
        conditions, params = _condiciones_fecha("pq.created_at", fecha_inicio, fecha_fin)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY pq.created_at DESC"

        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]

        if formato == "excel":
            for r in results:
                r["aprobado"] = "Sí" if r["aprobado"] else "No"
            columnas_excel = [
                ("Fecha Aprobación", "fecha_aprobacion"),
                ("Nombre", "nombre"),
                ("Documento", "documento"),
                ("Teléfono", "telefono"),
                ("Dirección", "direccion"),
                ("Sexo", "sexo"),
                ("Fecha Nacimiento", "fecha_nacimiento"),
                ("Comentarios Médico", "comentarios_medico"),
                ("Aprobado", "aprobado"),
            ]
            return generar_excel_response(results, columnas_excel, "ReporteMedico.xlsx")

        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener el reporte médico: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/reportes/medico/export/excel")
def reporte_medico_excel(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    current_user: dict = Depends(get_current_user_desde_query)
):
    return reporte_medico(fecha_inicio, fecha_fin, "excel", current_user)


# Pregunta del cuestionario PARQ que corresponde al consentimiento informado.
# La columna parq.consentimeinto nunca se escribe en el backend móvil (backFitness);
# la firma real se registra como respuesta a esta pregunta en respuesta_parq.
PREGUNTA_PARQ_CONSENTIMIENTO_ID = "79455ba5-2b19-4e7f-98d6-21dc468a357c"

@app.get("/reportes/consentimiento")
def reporte_consentimiento(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    formato: str = "json",
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Subconsulta: última respuesta de cada usuario a la pregunta de consentimiento
        # (puede responderse más de una vez si retoma el cuestionario).
        subquery = """
            SELECT DISTINCT ON ("userId")
                "userId",
                created_at AS fecha_firma,
                respuesta_parq AS firmo
            FROM public.respuesta_parq
            WHERE "preguntaParqId" = %s
        """
        params = [PREGUNTA_PARQ_CONSENTIMIENTO_ID]
        sub_conditions, sub_params = _condiciones_fecha("created_at", fecha_inicio, fecha_fin)
        if sub_conditions:
            subquery += " AND " + " AND ".join(sub_conditions)
            params.extend(sub_params)
        subquery += ' ORDER BY "userId", created_at DESC'

        # LEFT JOIN desde datos_generales: los usuarios que no han contestado la
        # pregunta también aparecen en el reporte, marcados como "Pendiente".
        query = f"""
        SELECT
            dg.first_name AS nombre,
            dg.document_number AS documento,
            dg.phone_number AS telefono,
            dg.address AS direccion,
            dg.gender::text AS sexo,
            dg.contacto_emergencia AS contacto_emergencia,
            rp.fecha_firma AS fecha_firma,
            rp.firmo AS firmo_consentimiento
        FROM public.datos_generales dg
        LEFT JOIN ({subquery}) rp ON rp."userId" = dg."userId"
        ORDER BY dg.first_name
        """

        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]

        if formato == "excel":
            for r in results:
                if r["firmo_consentimiento"] is True:
                    r["firmo_consentimiento"] = "Sí"
                elif r["firmo_consentimiento"] is False:
                    r["firmo_consentimiento"] = "No"
                else:
                    r["firmo_consentimiento"] = "Pendiente"
            columnas_excel = [
                ("Fecha Firma", "fecha_firma"),
                ("Nombre", "nombre"),
                ("Documento", "documento"),
                ("Teléfono", "telefono"),
                ("Dirección", "direccion"),
                ("Sexo", "sexo"),
                ("Contacto Emergencia", "contacto_emergencia"),
                ("Firmó Consentimiento", "firmo_consentimiento"),
            ]
            return generar_excel_response(results, columnas_excel, "ReporteFirmaConsentimiento.xlsx")

        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener el reporte de consentimiento: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/reportes/consentimiento/export/excel")
def reporte_consentimiento_excel(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    current_user: dict = Depends(get_current_user_desde_query)
):
    return reporte_consentimiento(fecha_inicio, fecha_fin, "excel", current_user)

@app.get("/reportes/asistencia-camiseta")
def reporte_asistencia_camiseta(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    formato: str = "json",
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        query = """
        SELECT
            dg.first_name AS nombre,
            dg.document_number AS documento,
            dg.phone_number AS telefono,
            dg.address AS direccion,
            dg.gender::text AS sexo,
            TO_CHAR(a.fecha, 'YYYY-MM') AS mes,
            COUNT(a.id) AS asistencias_mes
        FROM public.asistencia a
        JOIN public.actividade act ON act.id = a."actividadId"
        JOIN public.datos_generales dg ON dg.document_number = a.documento
        WHERE act.estado = true
        """
        conditions, params = _condiciones_fecha("a.fecha", fecha_inicio, fecha_fin)
        if conditions:
            query += " AND " + " AND ".join(conditions)
        query += """
        GROUP BY dg.first_name, dg.document_number, dg.phone_number, dg.address, dg.gender, TO_CHAR(a.fecha, 'YYYY-MM')
        ORDER BY dg.document_number, mes
        """

        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]

        if formato == "excel":
            columnas_excel = [
                ("Nombre", "nombre"),
                ("Documento", "documento"),
                ("Teléfono", "telefono"),
                ("Dirección", "direccion"),
                ("Sexo", "sexo"),
                ("Mes", "mes"),
                ("Asistencias en el Mes", "asistencias_mes"),
            ]
            return generar_excel_response(results, columnas_excel, "ReporteAsistenciaCamiseta.xlsx")

        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener el reporte de asistencia-camiseta: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/reportes/asistencia-camiseta/export/excel")
def reporte_asistencia_camiseta_excel(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    current_user: dict = Depends(get_current_user_desde_query)
):
    return reporte_asistencia_camiseta(fecha_inicio, fecha_fin, "excel", current_user)

@app.get("/asistencias/por-genero")
def obtener_asistencias_por_genero(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    fecha_especifica: str = None,
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta simplificada sin usuarios_unicos
        base_query = """
        SELECT 
            dg.gender::text as genero_normalizado,
            COUNT(a.id) as total_asistencias
        FROM public.asistencia a
        JOIN public.datos_generales dg ON dg.document_number = a.documento
        JOIN public.actividade act ON act.id = a."actividadId"
        WHERE act.estado = true
          AND dg.gender IS NOT NULL
        """
        
        conditions = []
        params = []
        
        # Filtros de fecha
        if fecha_especifica:
            conditions.append("a.fecha = %s")
            params.append(fecha_especifica)
        elif fecha_inicio and fecha_fin:
            conditions.append("a.fecha BETWEEN %s AND %s")
            params.extend([fecha_inicio, fecha_fin])
        elif fecha_inicio:
            conditions.append("a.fecha >= %s")
            params.append(fecha_inicio)
        elif fecha_fin:
            conditions.append("a.fecha <= %s")
            params.append(fecha_fin)
        
        if conditions:
            base_query += " AND " + " AND ".join(conditions)
        
        base_query += """
        GROUP BY dg.gender::text
        ORDER BY total_asistencias DESC
        """
        
        cur.execute(base_query, params)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        # Calcular totales generales
        total_asistencias = sum(r['total_asistencias'] for r in results)
        
        # Calcular porcentajes
        for result in results:
            if total_asistencias > 0:
                result['porcentaje_asistencias'] = round((result['total_asistencias'] / total_asistencias) * 100, 2)
            else:
                result['porcentaje_asistencias'] = 0
        
        return {
            "asistencias_por_genero": results,
            "estadisticas_generales": {
                "total_asistencias": total_asistencias,
                "total_generos": len(results)
            },
            "filtros": {
                "fecha_inicio": fecha_inicio,
                "fecha_fin": fecha_fin,
                "fecha_especifica": fecha_especifica
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener asistencias por género: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/asistencias/por-edad-completo")
def obtener_asistencias_por_edad_completo(
    fecha_inicio: str = None,
    fecha_fin: str = None,
    fecha_especifica: str = None,
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta simplificada
        base_query = """
        SELECT 
            CASE 
                WHEN dg.birth_date IS NULL THEN 'Sin fecha de nacimiento'
                WHEN EXTRACT(YEAR FROM AGE(CURRENT_DATE, dg.birth_date)) < 18 THEN 'Menor de 18'
                WHEN EXTRACT(YEAR FROM AGE(CURRENT_DATE, dg.birth_date)) BETWEEN 18 AND 25 THEN '18-25 años'
                WHEN EXTRACT(YEAR FROM AGE(CURRENT_DATE, dg.birth_date)) BETWEEN 26 AND 35 THEN '26-35 años'
                WHEN EXTRACT(YEAR FROM AGE(CURRENT_DATE, dg.birth_date)) BETWEEN 36 AND 45 THEN '36-45 años'
                WHEN EXTRACT(YEAR FROM AGE(CURRENT_DATE, dg.birth_date)) BETWEEN 46 AND 55 THEN '46-55 años'
                WHEN EXTRACT(YEAR FROM AGE(CURRENT_DATE, dg.birth_date)) BETWEEN 56 AND 65 THEN '56-65 años'
                WHEN EXTRACT(YEAR FROM AGE(CURRENT_DATE, dg.birth_date)) > 65 THEN '66+ años'
                ELSE 'Edad no válida'
            END as rango_edad,
            COUNT(a.id) as total_asistencias
        FROM public.asistencia a
        LEFT JOIN public.datos_generales dg ON dg.document_number = a.documento
        JOIN public.actividade act ON act.id = a."actividadId"
        WHERE act.estado = true
        """
        
        conditions = []
        params = []
        
        # Filtros de fecha
        if fecha_especifica:
            conditions.append("a.fecha = %s")
            params.append(fecha_especifica)
        elif fecha_inicio and fecha_fin:
            conditions.append("a.fecha BETWEEN %s AND %s")
            params.extend([fecha_inicio, fecha_fin])
        elif fecha_inicio:
            conditions.append("a.fecha >= %s")
            params.append(fecha_inicio)
        elif fecha_fin:
            conditions.append("a.fecha <= %s")
            params.append(fecha_fin)
        
        if conditions:
            base_query += " AND " + " AND ".join(conditions)
        
        base_query += """
        GROUP BY 1
        ORDER BY 2 DESC
        """
        
        cur.execute(base_query, params)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        # Calcular totales generales
        total_asistencias = sum(r['total_asistencias'] for r in results)
        
        # Calcular porcentajes
        for result in results:
            if total_asistencias > 0:
                result['porcentaje_asistencias'] = round((result['total_asistencias'] / total_asistencias) * 100, 2)
            else:
                result['porcentaje_asistencias'] = 0
        
        return {
            "asistencias_por_edad": results,
            "estadisticas_generales": {
                "total_asistencias": total_asistencias,
                "total_rangos_edad": len(results)
            },
            "filtros": {
                "fecha_inicio": fecha_inicio,
                "fecha_fin": fecha_fin,
                "fecha_especifica": fecha_especifica
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener asistencias por edad: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/monitores")
def obtener_monitores(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para obtener monitores con datos completos
        query = """
        SELECT 
            u.id as user_id,
            u.usuario,
            u.email,
            dg.first_name,
            u.role,
            dg.document_number,
            dg.gender::text as gender
        FROM security.users u
        INNER JOIN public.datos_generales dg ON dg."userId" = u.id
        WHERE 'monitor' = ANY(u.role)
          AND u.is_active = true
        """
        
        cur.execute(query)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        return {
            "monitores": results,
            "total": len(results),
            "message": f"Se encontraron {len(results)} monitores activos con datos completos"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener monitores: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/monitores/basico")
def obtener_monitores_basico(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta simplificada con solo los campos solicitados
        query = """
        SELECT 
            u.id as user_id,
            dg.first_name,
            dg.document_number
        FROM security.users u
        INNER JOIN public.datos_generales dg ON dg."userId" = u.id
        WHERE 'monitor' = ANY(u.role)
          AND u.is_active = true
        ORDER BY dg.first_name ASC
        """
        
        cur.execute(query)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        return {
            "monitores": results,
            "total": len(results)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener monitores básico: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/monitores/{user_id}")
def obtener_monitor_por_id(user_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para obtener un monitor específico con datos completos
        query = """
        SELECT 
            u.id as user_id,
            u.usuario,
            u.email,
            u.role,
            u.is_active,
            u.created_at,
            dg.first_name,
            dg.last_name,
            dg.document_number,
            dg.phone_number,
            dg.gender::text as gender,
            dg.birth_date,
            dg.address,
            dg.email as datos_email
        FROM security.users u
        INNER JOIN public.datos_generales dg ON dg."userId" = u.id
        WHERE u.id = %s
          AND 'monitor' = ANY(u.role)
          AND u.is_active = true
        """
        
        cur.execute(query, (user_id,))
        columns = [desc[0] for desc in cur.description]
        result = cur.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Monitor no encontrado")
        
        monitor = dict(zip(columns, result))
        
        return {
            "monitor": monitor,
            "message": f"Monitor {monitor['first_name']} {monitor['last_name']} encontrado"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener monitor: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/monitores/search")
def buscar_monitores(
    nombre: str = None,
    documento: str = None,
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta base con JOIN obligatorio
        base_query = """
        SELECT 
            u.id as user_id,
            u.usuario,
            dg.first_name,
            dg.last_name,
            dg.document_number,
            dg.phone_number,
            dg.gender::text as gender
        FROM security.users u
        INNER JOIN public.datos_generales dg ON dg."userId" = u.id
        WHERE 'monitor' = ANY(u.role)
          AND u.is_active = true
        """
        
        conditions = []
        params = []
        
        # Filtro por nombre (first_name o last_name)
        if nombre:
            conditions.append("(LOWER(dg.first_name) LIKE LOWER(%s) OR LOWER(dg.last_name) LIKE LOWER(%s))")
            params.extend([f"%{nombre}%", f"%{nombre}%"])
        
        # Filtro por documento
        if documento:
            conditions.append("dg.document_number LIKE %s")
            params.append(f"%{documento}%")
        
        # Añadir condiciones a la consulta
        if conditions:
            base_query += " AND " + " AND ".join(conditions)
        
        base_query += " ORDER BY dg.first_name ASC, dg.last_name ASC"
        
        cur.execute(base_query, params)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        return {
            "monitores": results,
            "total": len(results),
            "filtros": {
                "nombre": nombre,
                "documento": documento
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al buscar monitores: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/monitores/con-actividades")
def obtener_monitores_con_actividades(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para obtener monitores con sus estadísticas de actividades
        query = """
        SELECT 
            u.id as user_id,
            u.usuario,
            dg.first_name,
            dg.last_name,
            dg.document_number,
            dg.phone_number,
            COUNT(act.id) as total_actividades,
            COUNT(CASE WHEN act.estado = true THEN 1 END) as actividades_activas,
            COUNT(CASE WHEN act.estado = false THEN 1 END) as actividades_inactivas
        FROM security.users u
        INNER JOIN public.datos_generales dg ON dg."userId" = u.id
        LEFT JOIN public.actividade act ON act."userId" = u.id
        WHERE 'monitor' = ANY(u.role)
          AND u.is_active = true
        GROUP BY u.id, u.usuario, dg.first_name, dg.last_name, dg.document_number, dg.phone_number
        ORDER BY total_actividades DESC, dg.first_name ASC
        """
        
        cur.execute(query)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        return {
            "monitores_con_actividades": results,
            "total": len(results),
            "message": f"Se encontraron {len(results)} monitores con estadísticas de actividades"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener monitores con actividades: {str(e)}")
    finally:
        cur.close()
        conn.close()

# estadisticas monitores

@app.get("/monitores/actividades-canceladas/{user_id}")
def obtener_actividades_canceladas_monitor(
    user_id: str, 
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para obtener actividades canceladas de un monitor específico
        query = """
        SELECT 
            act.id,
            act.descripcion,
            act.fecha,
            act.hora,
            act."motivoCancelado",
            act.created_at,
            act.updated_at,
            act.estado,
            ta.nombre as tipo_actividad,
            p.nombre as parque_nombre,
            b.nombre as barrio_nombre,
            c.nombre as comuna_nombre,
            dg.first_name as monitor_nombre,
            dg.document_number as monitor_documento
        FROM public.actividade act
        INNER JOIN security.users u ON u.id = act."userId"
        INNER JOIN public.datos_generales dg ON dg."userId" = u.id
        LEFT JOIN public.tipo_actividad ta ON ta.id = act."tipoActividadId"
        LEFT JOIN public.parque p ON p.id = act."parqueId"
        LEFT JOIN public.barrio b ON b.id = p."barrioId"
        LEFT JOIN public.comuna_corregimiento c ON c.id = b."comunaCorregimientoId"
        WHERE act."userId" = %s
          AND act.estado = false
          AND 'monitor' = ANY(u.role)
          AND u.is_active = true
        ORDER BY act.fecha DESC, act.hora DESC
        """
        
        cur.execute(query, (user_id,))
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        # Verificar si el monitor existe
        if not results:
            # Verificar si el usuario existe y es monitor
            cur.execute("""
                SELECT u.id, dg.first_name
                FROM security.users u
                LEFT JOIN public.datos_generales dg ON dg."userId" = u.id
                WHERE u.id = %s AND 'monitor' = ANY(u.role) AND u.is_active = true
            """, (user_id,))
            
            monitor_exists = cur.fetchone()
            if not monitor_exists:
                raise HTTPException(status_code=404, detail="Monitor no encontrado")
        
        return {
            "actividades_canceladas": results,
            "total": len(results),
            "monitor_id": user_id,
            "message": f"Se encontraron {len(results)} actividades canceladas"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener actividades canceladas: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/monitores/estadisticas-actividades/{user_id}")
def obtener_estadisticas_actividades_monitor(
    user_id: str, 
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para obtener estadísticas completas de actividades del monitor
        query = """
        SELECT 
            COUNT(*) as total_actividades,
            COUNT(CASE WHEN estado = true THEN 1 END) as actividades_activas,
            COUNT(CASE WHEN estado = false THEN 1 END) as actividades_canceladas,
            dg.first_name,
            dg.document_number
        FROM public.actividade act
        INNER JOIN security.users u ON u.id = act."userId"
        INNER JOIN public.datos_generales dg ON dg."userId" = u.id
        WHERE act."userId" = %s
          AND 'monitor' = ANY(u.role)
          AND u.is_active = true
        GROUP BY dg.first_name, dg.document_number
        """
        
        cur.execute(query, (user_id,))
        result = cur.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Monitor no encontrado")
        
        total, activas, canceladas, nombre, documento = result
        
        return {
            "monitor": {
                "user_id": user_id,
                "nombre": nombre,
                "documento": documento
            },
            "estadisticas": {
                "total_actividades": total,
                "actividades_activas": activas,
                "actividades_canceladas": canceladas,
                "porcentaje_canceladas": round((canceladas / total * 100), 2) if total > 0 else 0
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener estadísticas: {str(e)}")
    finally:
        cur.close()
        conn.close()

# promedio de actividades

@app.get("/monitores/calificaciones-promedio/{user_id}")
def obtener_promedio_calificaciones_monitor(
    user_id: str, 
    current_user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta para obtener promedio de calificaciones por actividad del monitor
        query = """
        SELECT 
            act.id as actividad_id,
            act.descripcion,
            act.fecha,
            act.hora,
            act.estado,
            ta.nombre as tipo_actividad,
            p.nombre as parque_nombre,
            COUNT(c.id) as total_calificaciones,
            ROUND(AVG(c.calificacion), 2) as promedio_calificacion,
            MIN(c.calificacion) as calificacion_minima,
            MAX(c.calificacion) as calificacion_maxima,
            dg.first_name as monitor_nombre,
            dg.document_number as monitor_documento
        FROM public.actividade act
        INNER JOIN security.users u ON u.id = act."userId"
        INNER JOIN public.datos_generales dg ON dg."userId" = u.id
        LEFT JOIN public.tipo_actividad ta ON ta.id = act."tipoActividadId"
        LEFT JOIN public.parque p ON p.id = act."parqueId"
        LEFT JOIN public.calificacion c ON c."actividadId" = act.id
        WHERE act."userId" = %s
          AND 'monitor' = ANY(u.role)
          AND u.is_active = true
        GROUP BY 
            act.id, act.descripcion, act.fecha, act.hora, act.estado,
            ta.nombre, p.nombre, dg.first_name, dg.document_number
        ORDER BY act.fecha DESC, act.hora DESC
        """
        
        cur.execute(query, (user_id,))
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        # Verificar si el monitor existe
        if not results:
            # Verificar si el usuario existe y es monitor
            cur.execute("""
                SELECT u.id, dg.first_name, dg.document_number
                FROM security.users u
                LEFT JOIN public.datos_generales dg ON dg."userId" = u.id
                WHERE u.id = %s AND 'monitor' = ANY(u.role) AND u.is_active = true
            """, (user_id,))
            
            monitor_exists = cur.fetchone()
            if not monitor_exists:
                raise HTTPException(status_code=404, detail="Monitor no encontrado")
            
            # El monitor existe pero no tiene actividades
            return {
                "actividades_con_calificaciones": [],
                "total_actividades": 0,
                "monitor_id": user_id,
                "monitor_nombre": monitor_exists[1],
                "monitor_documento": monitor_exists[2],
                "message": "El monitor no tiene actividades registradas"
            }
        
        # Filtrar solo actividades que tienen calificaciones
        actividades_con_calificaciones = [r for r in results if r['total_calificaciones'] > 0]
        
        # Calcular estadísticas generales
        if actividades_con_calificaciones:
            promedio_general = round(
                sum(r['promedio_calificacion'] * r['total_calificaciones'] for r in actividades_con_calificaciones) /
                sum(r['total_calificaciones'] for r in actividades_con_calificaciones), 2
            )
            total_calificaciones_general = sum(r['total_calificaciones'] for r in actividades_con_calificaciones)
        else:
            promedio_general = 0
            total_calificaciones_general = 0
        
        return {
            "actividades_con_calificaciones": actividades_con_calificaciones,
            "total_actividades": len(results),
            "actividades_calificadas": len(actividades_con_calificaciones),
            "monitor_id": user_id,
            "monitor_nombre": results[0]['monitor_nombre'] if results else None,
            "monitor_documento": results[0]['monitor_documento'] if results else None,
            "estadisticas_generales": {
                "promedio_general": promedio_general,
                "total_calificaciones": total_calificaciones_general,
                "actividades_sin_calificar": len(results) - len(actividades_con_calificaciones)
            },
            "message": f"Se encontraron {len(actividades_con_calificaciones)} actividades con calificaciones"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener calificaciones: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/caracterizacion-por-zona")
def obtener_caracterizacion_por_zona(current_user: dict = Depends(get_current_user)):
    """
    Endpoint para obtener datos de la vista vista_caracterizacion_por_zona
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Consulta a la vista vista_caracterizacion_por_zona
        query = """
        SELECT * FROM vista_caracterizacion_por_zona
        ORDER BY zona ASC
        """
        
        cur.execute(query)
        columns = [desc[0] for desc in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
        return {
            "caracterizacion_por_zona": results,
            "total": len(results),
            "message": f"Se encontraron {len(results)} registros de caracterización por zona"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener caracterización por zona: {str(e)}")
    finally:
        cur.close()
        conn.close()

# @app.get("/caracterizacion-por-zona/filtrada")
# def obtener_caracterizacion_por_zona_filtrada(
#     zona: str = None,
#     fecha_inicio: str = None,
#     fecha_fin: str = None,
#     current_user: dict = Depends(get_current_user)
# ):
#     """
#     Endpoint para obtener datos filtrados de la vista vista_caracterizacion_por_zona
#     """
#     conn = get_connection()
#     cur = conn.cursor()
#     try:
#         # Consulta base a la vista
#         base_query = """
#         SELECT * FROM vista_caracterizacion_por_zona
#         WHERE 1=1
#         """
        
#         conditions = []
#         params = []
        
#         # Filtro por zona
#         if zona:
#             conditions.append("LOWER(zona) LIKE LOWER(%s)")
#             params.append(f"%{zona}%")
        
#         # Filtros de fecha (asumiendo que existe una columna de fecha en la vista)
#         if fecha_inicio and fecha_fin:
#             conditions.append("fecha BETWEEN %s AND %s")
#             params.extend([fecha_inicio, fecha_fin])
#         elif fecha_inicio:
#             conditions.append("fecha >= %s")
#             params.append(fecha_inicio)
#         elif fecha_fin:
#             conditions.append("fecha <= %s")
#             params.append(fecha_fin)
        
#         # Añadir condiciones a la consulta
#         if conditions:
#             base_query += " AND " + " AND ".join(conditions)
        
#         base_query += " ORDER BY zona ASC"
        
#         cur.execute(base_query, params)
#         columns = [desc[0] for desc in cur.description]
#         results = [dict(zip(columns, row)) for row in cur.fetchall()]
        
#         return {
#             "caracterizacion_por_zona": results,
#             "total": len(results),
#             "filtros": {
#                 "zona": zona,
#                 "fecha_inicio": fecha_inicio,
#                 "fecha_fin": fecha_fin
#             },
#             "message": f"Se encontraron {len(results)} registros con los filtros aplicados"
#         }
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Error al obtener caracterización filtrada: {str(e)}")
#     finally:
#         cur.close()
#         conn.close()




