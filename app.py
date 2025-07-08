from fastapi import FastAPI, HTTPException, Depends, status, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta
import psycopg2
import os
from dotenv import load_dotenv

# App y CORS
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fitness-dash.celenius.store", "http://localhost:5173","https://frontfitnessdashboard.onrender.com"],
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

def get_current_user(token: str = Depends(oauth2_scheme)):
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



