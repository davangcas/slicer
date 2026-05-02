# Slicer estimate API

API HTTP en **FastAPI** que estima **tiempo de impresión** (horas y minutos) y **masa aproximada de filamento** (gramos) a partir de un modelo **STL** u **OBJ** y un **material**, usando **CuraEngine** (Cura 4.x) en contenedor.

## Requisitos

- Docker y Docker Compose v2

## Puesta en marcha

1. Opcional: copia variables de entorno.

   ```bash
   cp .env.example .env
   ```

   `docker-compose.yml` carga `.env` si existe (`required: false`).

2. Arranca el servicio (puerto **8050**).

   ```bash
   docker compose up --build
   ```

3. Documentación interactiva: [http://localhost:8050/docs](http://localhost:8050/docs)

## Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/health` | Comprobación simple (`{"status":"ok"}`). Sin autenticación. |
| `POST` | `/oauth/token` | OAuth 2.0 **client credentials** (RFC 6749 §4.4): devuelve `access_token` JWT. Solo si `API_AUTH_ENABLED=true`. |
| `GET` | `/machines` | Lista definiciones de máquina Cura disponibles (`id`, `name`). Con auth activada: cabecera `Authorization: Bearer <token>`. |
| `POST` | `/estimate` | Sube modelo + material (+ máquina opcional); devuelve estimación. Con auth activada: `Bearer`. |

### Autenticación (OAuth2 client credentials)

Con **`API_AUTH_ENABLED=true`** debes definir **`OAUTH_JWT_SECRET`** (secreto para firmar JWT; usa un valor largo y aleatorio) y al menos un cliente:

- **`OAUTH_CLIENT_ID`** y **`OAUTH_CLIENT_SECRET`**, o
- **`OAUTH_CLIENTS_JSON`**: objeto JSON `{"client_id":"client_secret", ...}`.

1. Obtener token (`application/x-www-form-urlencoded`):

   ```bash
   curl -sS -X POST http://localhost:8050/oauth/token \
     -H 'Content-Type: application/x-www-form-urlencoded' \
     -d 'grant_type=client_credentials&client_id=client-id&client_secret=client-secret'
   ```

2. Llamar a la API con el JWT:

   ```bash
   TOKEN=...   # access_token de la respuesta anterior
   curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8050/machines
   ```

Con **`API_AUTH_ENABLED=false`** (por defecto) el comportamiento es el anterior: `/machines` y `/estimate` no exigen cabecera. El arranque falla si activas auth pero faltan secreto JWT o clientes configurados.

En **OpenAPI** (`/docs`) aparece el esquema *OAuth2ClientCredentials* para autorizar peticiones protegidas.

### `POST /estimate`

- **Content-Type**: `multipart/form-data`
- **Campos**:
  - `source` (archivo): extensión `.stl` o `.obj` (sin distinguir mayúsculas).
  - `material` (texto): uno de **PLA**, **PETG**, **ABS**, **ASA**, **TPU**.
  - `machine` (texto, opcional): `id` devuelto por `GET /machines`. Si se omite, se usa la ruta de **`CURA_MACHINE_DEF`**.

**Respuesta** (`application/json`):

```json
{ "hours": 0, "minutes": 6, "grams": 1.08 }
```

Los gramos se calculan a partir del volumen de filamento que reporta CuraEngine y una **densidad convencional** por material; sirven como estimación, no como peso real en báscula.

**Errores habituales**: `422` (archivo/material/máquina inválidos), `502` (fallo de CuraEngine o error inesperado), `504` (timeout de slice).

### Ejemplos con `curl`

```bash
curl -sS http://localhost:8050/machines | head

curl -sS -F "source=@test_cube.stl" -F "material=PLA" http://localhost:8050/estimate

curl -sS -F "source=@test_cube.stl" -F "material=PLA" -F "machine=ultimaker2" http://localhost:8050/estimate
```

## Variables de entorno

Definidas en [`.env.example`](.env.example); el proceso las lee en tiempo de ejecución.

| Variable | Rol |
|----------|-----|
| `API_AUTH_ENABLED` | `true` para exigir JWT en `/machines` y `/estimate` (por defecto `false`). |
| `OAUTH_JWT_SECRET` | Secreto HMAC para firmar access tokens (obligatorio si auth activa). |
| `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` | Par cliente/servidor para el flujo client credentials. |
| `OAUTH_CLIENTS_JSON` | Alternativa: varios clientes en un único JSON. |
| `OAUTH_ACCESS_TOKEN_EXPIRE_MINUTES` | Caducidad del access token (por defecto `60`). |
| `API_RATE_LIMIT_TOKEN` | Límite slowapi para `POST /oauth/token` (por defecto `30/minute`). |
| `SLICE_TIMEOUT` | Segundos máximos por invocación a CuraEngine (por defecto `600`). |
| `CURA_ENGINE_BIN` | Ejecutable de CuraEngine (por defecto `CuraEngine`). |
| `CURA_MACHINE_DEF` | Ruta al `.def.json` por defecto cuando no se envía `machine` en `/estimate`. |
| `CURA_ENGINE_SEARCH_PATH` | Rutas de recursos Cura (`definitions`, extrusores, materiales…), separadas por `:` o `;`. |

En la imagen Docker, los recursos de definiciones se copian bajo `/opt/cura/resources`; `fdm-materials` aporta materiales bajo `/usr/share/cura/resources`.

## Detalles de implementación

- **CuraEngine** se invoca en modo consola (`slice -v -p`); el tiempo y el volumen de filamento se leen del **log en stderr**, no del encabezado del G-code.
- **OBJ**: CuraEngine 4.x solo carga STL en `-l`; el servicio convierte OBJ→STL con **trimesh** antes de cortar.
- **`GET /machines`**: recorre `definitions/*.def.json` en cada raíz de `CURA_ENGINE_SEARCH_PATH` y excluye definiciones base `fdmprinter` y `fdmextruder`.

## Estructura del repositorio

- `app/main.py` — Rutas FastAPI.
- `app/auth.py` — OAuth2 client credentials y validación de JWT.
- `app/slicer_service.py` — Invocación a CuraEngine y parseo de resultados.
- `app/machines.py` — Catálogo de máquinas y resolución de rutas `-j`.
- `Dockerfile` / `docker-compose.yml` — Imagen Debian Bookworm con `cura-engine` y recursos Cura extraídos del paquete `cura`.
