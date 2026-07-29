# Despliegue a producción (VPS Contabo)

## Infraestructura actual

- **Servidor:** VPS Contabo, usuario `bg3sas`, host `vmi2568569`
- **Ruta del proyecto:** `/var/www/back-rodrigo-fitness`
- **Repo remoto:** `https://github.com/exde17/back-rodrigo-fitness.git`
- **Entorno virtual:** `/var/www/back-rodrigo-fitness/venv` (Python 3.12)
- **Cómo corre la API:** `gunicorn` con 4 workers de tipo `uvicorn.workers.UvicornWorker`, escuchando en `127.0.0.1:8000` (por detrás de un proxy/reverse proxy hacia `fitness-dash.celenius.store`).
- **Proceso lanzado manualmente** con `nohup` (NO hay systemd, supervisor ni pm2 configurado todavía). El log de salida queda en `nohup.out` dentro del proyecto.

Comando exacto con el que se levantó el proceso (por si algún día hay que arrancarlo desde cero):
```bash
cd /var/www/back-rodrigo-fitness
nohup venv/bin/gunicorn app:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 127.0.0.1:8000 > nohup.out 2>&1 &
disown
```

## Pasos normales para desplegar un cambio

1. **Conéctate al VPS y revisa el estado antes de tocar nada:**
   ```bash
   cd /var/www/back-rodrigo-fitness
   git status
   ```
   Si dice "nothing to commit, working tree clean", sigue al paso 2.
   Si muestra archivos modificados sin commitear, **no los descartes a ciegas** — revisa qué son (`git diff <archivo>`). Si son cambios reales hechos a mano en el servidor (como pasó una vez con un origen CORS agregado directo ahí), avísame para incorporarlos al repo antes de perderlos.

2. **Trae los cambios:**
   ```bash
   git pull
   ```

3. **¿Cambió `requirements.txt`?** Si sí, instala las dependencias nuevas con el pip del venv (no uses `pip` del sistema, Debian/Ubuntu lo bloquea con "externally-managed-environment"):
   ```bash
   venv/bin/pip install -r requirements.txt
   ```

4. **Recarga el servicio en caliente** (sin cortar el servicio — gunicorn levanta workers nuevos con el código actualizado y solo entonces mata los viejos):
   ```bash
   ps aux | grep gunicorn        # busca el PID del proceso master (el que tiene menos CPU acumulada / es el "padre")
   kill -HUP <PID_DEL_MASTER>    # reemplaza <PID_DEL_MASTER> por el número real, sin los < >
   ```

5. **Verifica que arrancó bien:**
   ```bash
   tail -f nohup.out
   ```
   Busca `Application startup complete` sin tracebacks debajo. `Ctrl+C` para salir del `tail` cuando confirmes.

6. **Prueba en vivo:** entra al dashboard y prueba el flujo afectado (login, un reporte, el mapa, etc.).

## Problemas ya conocidos y cómo resolverlos

### "error: cannot pull with rebase: You have unstaged changes"
Alguien (o algo) modificó un archivo trackeado directo en el servidor. Antes de hacer nada:
```bash
git status
git diff <archivo>
```
- Si es basura sin importancia (ej. un `.pyc` regenerado, ya no debería pasar más porque `__pycache__/` está en `.gitignore` desde el commit `deca6a0`): `git restore <archivo>` y listo.
- Si es un cambio real (como el origen CORS que se agregó a mano una vez): avísame para meterlo al repo como corresponde, en vez de perderlo con un `restore`.

### Conflictos tipo "CONFLICT (modify/delete)" al hacer `git pull`
Esto pasó porque el VPS tenía commits locales propios nunca subidos a GitHub (con mensajes tipo "rar", "pp" — probablemente de commits accidentales hechos ahí mismo alguna vez). El `pull` en este servidor usa `rebase`, así que intenta reaplicar esos commits locales y chocan.
Si el conflicto es por un archivo que ya no debería existir (como los `.pyc`):
```bash
git rm -f <archivo_en_conflicto>
git rebase --continue
```
Repite si aparece otro conflicto igual (puede haber varios commits sueltos en fila). Si el editor se abre pidiendo confirmar un mensaje de commit, guarda y sal sin cambiar nada (`Ctrl+O`, `Enter`, `Ctrl+X` en nano; `:wq` en vim).

Si el conflicto es en un archivo real (no basura), **para y avísame** antes de resolverlo — no lo descartes solo.

### El venv está roto (`pip`/`python3` "No such file or directory" en `venv/bin/`)
Ya nos pasó una vez: algo dejó el venv con solo la carpeta `include/`, sin `bin/` ni `lib/`. Se reconstruye así:
```bash
rm -rf /var/www/back-rodrigo-fitness/venv
python3 -m venv /var/www/back-rodrigo-fitness/venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
venv/bin/pip install gunicorn   # gunicorn NO está en requirements.txt, hay que instalarlo aparte (ver pendientes abajo)
```
Confirma que quedaron los binarios antes de reiniciar:
```bash
ls venv/bin/ | grep -E "python3|gunicorn|pip"
```

### Nuevo dominio de frontend da error de CORS
Si un frontend nuevo (Vercel, Render, dominio propio, etc.) no puede loguearse y la consola muestra "blocked by CORS policy", hay que agregar su origen a `allow_origins` en `app.py` (línea ~19), commitear, pushear, y desplegar con los pasos de arriba.

### Frontend en Vercel muestra `undefinedlogin/` en las peticiones
Significa que a ese proyecto de Vercel le faltan las variables de entorno `VITE_API_URL` y `VITE_EXCEL_URL` (Vite las incluye en el build, no en runtime — hay que configurarlas en Settings → Environment Variables del proyecto en Vercel, y luego hacer Redeploy).

## Pendientes conocidos (no urgentes, pero hay que resolverlos en algún momento)

- **`.env` sigue versionado en git** (aunque ya está en `.gitignore`, eso solo evita que se vuelva a trackear si se borra del índice — el archivo ya committeado sigue ahí). Esto es un riesgo: si el `.env` real de producción difiere del que está en el repo (bastante probable, tiene credenciales reales), cualquier `git pull` futuro puede volver a chocar con "unstaged changes", igual que pasó con `__pycache__`. Arreglo pendiente: sacarlo del control de versiones (`git rm --cached .env`) — **no lo hagas sin avisar primero**, porque si no se coordina bien puede borrar el `.env` real del servidor al aplicar el pull.
- **`gunicorn` no está en `requirements.txt`**, aunque es lo que corre el servicio en producción. Si se reconstruye el venv siguiendo solo `requirements.txt`, falta instalarlo aparte (ver sección de venv roto arriba).
- **No hay systemd/supervisor.** El proceso corre con `nohup` manual — si el VPS se reinicia, la API no vuelve a levantar sola hasta que alguien la arranque a mano. Recomendado pasar esto a un servicio `systemd` con restart automático.
