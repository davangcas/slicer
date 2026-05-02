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
| `GET` | `/health` | Comprobación simple (`{"status":"ok"}`). |
| `GET` | `/machines` | Lista definiciones de máquina Cura disponibles (`id`, `name`). |
| `POST` | `/estimate` | Sube modelo + material (+ máquina opcional); devuelve estimación. |

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
- `app/slicer_service.py` — Invocación a CuraEngine y parseo de resultados.
- `app/machines.py` — Catálogo de máquinas y resolución de rutas `-j`.
- `Dockerfile` / `docker-compose.yml` — Imagen Debian Bookworm con `cura-engine` y recursos Cura extraídos del paquete `cura`.
