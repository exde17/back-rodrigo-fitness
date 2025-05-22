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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load env
load_dotenv()
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