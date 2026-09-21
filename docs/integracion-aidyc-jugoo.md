# Integración AIDYC ↔ Jugoo (fase 1)

## 1. Arquitectura

```
AIDYC (fuente de verdad)
  datos/{padre_db}/tareas.json
  + flock + version
        │
        ▼
/api/integracion/jugoo/v1/*   (API key, tenant fijo)
        │
        ▼
Jugoo AidycClient → AidycTaskProvider → cache local
        │
        ▼
UI Tareas → pestañas Locales | AIDYC
```

- Las **tareas locales** de Jugoo siguen en `tasks.json` (`LocalTaskProvider` / `TasksService`).
- Las tareas **AIDYC** se muestran desde un cache/link (`~/.local/share/waybar-shell/aidyc_tasks_cache.json`).
- La fuente de verdad de AIDYC **sigue siendo AIDYC**.

## 2. Autenticación

Header:

```
X-API-Key: <JUGOO_API_KEY>
```

Variables en AIDYC (`.env`):

| Variable | Uso |
|----------|-----|
| `JUGOO_API_KEY` | Clave de servicio |
| `JUGOO_PADRE_DB` | Negocio permitido (tenant) |
| `JUGOO_ACTOR_USUARIO` | Usuario AIDYC con el que actúa Jugoo |
| `JUGOO_ACTOR_NOMBRE` | Nombre display |
| `JUGOO_ACTOR_TIPO` | Rol (`auxiliar`, `admin`, `gerente`, …) |

El actor **no se convierte en admin automáticamente**: los permisos de asignación siguen el `Tipo` configurado (`GESTORES_TAREAS = admin|adminbasico|gerente`).

Si el cliente envía `padre_db` distinto de `JUGOO_PADRE_DB` → **403**.

## 3. Endpoints

Prefijo: `/api/integracion/jugoo/v1`

| Method | Path | Auth |
|--------|------|------|
| GET | `/health` | no |
| GET | `/tareas` | API key |
| GET | `/tareas/<id>` | API key |
| POST | `/tareas` | API key |
| PUT | `/tareas/<id>` | API key |
| POST | `/tareas/<id>/completar` | API key |
| POST | `/tareas/<id>/reabrir` | API key |
| POST | `/tareas/<id>/posponer` | API key |
| GET | `/usuarios` | API key |

Query útiles en listado: `estado`, `prioridad`, `categoria`, `asignado`, `since`, `limit`.

## 4. Payloads

Crear:

```json
{
  "titulo": "Revisar inventario",
  "descripcion": "...",
  "categoria": "inventario",
  "prioridad": "alta",
  "fecha_limite": "2026-09-25",
  "asignado_a": "maria"
}
```

Actualizar (optimistic lock):

```json
{
  "titulo": "Nuevo título",
  "expected_version": 3
}
```

Conflicto → **HTTP 409** con `code=version_conflict` y `current_version`.

## 5. Mapping de estados

| AIDYC | Jugoo |
|-------|-------|
| `pendiente` | `pending` (o `overdue` si `fecha_limite` < hoy) |
| `completada` | `completed` |

**No existe** `en_progreso` en AIDYC; Jugoo no lo inventa.

## 6. Mapping de usuarios

- Identidad AIDYC: `Usuarios.Usuario` (string).
- Endpoint `/usuarios` expone `usuario`, `nombre`, `email` (si existe), `tipo`.
- Jugoo usa esos valores en el selector de asignación.
- Mapping Jugoo↔AIDYC en esta fase: el actor configurado en servidor (`JUGOO_ACTOR_*`).

## 7. Versionado

- Campo entero `version` en cada tarea.
- Alta → `version = 1`.
- Cada mutación real incrementa `version` y actualiza `fecha_actualizacion`.
- Tareas antiguas sin campo → se tratan como `version = 1` al leer (en memoria).

## 8. Sincronización

Solo **pull** en esta fase:

1. Jugoo llama `GET /tareas`.
2. Actualiza `aidyc_tasks_cache.json`.
3. UI refresca.
4. Guarda `last_sync`.

Poll mientras el panel AIDYC está abierto (`aidyc.poll_sec`, default 60s).

No hay webhooks / SSE / WebSockets todavía.

## 9. Manejo de errores

| Código | Comportamiento Jugoo |
|--------|----------------------|
| 401 | `AidycAuthError`, banner desconectado |
| 403 | `AidycForbiddenError` |
| 404 | `AidycNotFoundError` |
| 409 | `AidycConflictError` → no sobrescribe; pide sync |
| Timeout/red | cache local se conserva; tareas locales intactas |

Si AIDYC cae, las tareas **Locales** siguen funcionando.

## 10. Configuración Jugoo

En **Configuraciones → Avanzado → AIDYC**:

| Setting | Descripción |
|---------|-------------|
| `aidyc.enabled` | Activa integración |
| `aidyc.base_url` | URL del servidor |
| `aidyc.api_key` | Misma que `JUGOO_API_KEY` |
| `aidyc.padre_db` | Mismo que `JUGOO_PADRE_DB` |
| `aidyc.timeout_sec` | Timeout HTTP |
| `aidyc.poll_sec` | Intervalo pull |

## 11. Cómo ejecutar localmente

### AIDYC

```bash
cd AidyC-web
cp env.example .env   # si aún no existe
# Editar:
# JUGOO_API_KEY=dev-key
# JUGOO_PADRE_DB=tu_padre
# JUGOO_ACTOR_USUARIO=tu_usuario
# JUGOO_ACTOR_TIPO=admin   # o auxiliar
python Aidyc_app.py
```

Probar health:

```bash
curl -s http://127.0.0.1:5000/api/integracion/jugoo/v1/health
curl -s -H "X-API-Key: dev-key" \
  http://127.0.0.1:5000/api/integracion/jugoo/v1/tareas
```

### Jugoo

1. Abrir Configuraciones → Avanzado.
2. Activar integración y completar URL / API key / padre.
3. Abrir panel Tareas → pestaña **AIDYC**.
4. Sync / crear / completar.

## 12. Cómo probar

### AIDYC

```bash
cd AidyC-web
.venv/bin/python -m pytest tests/test_integracion_jugoo.py -q
```

### Jugoo

```bash
cd jugoo
python -m pytest shell/tests/test_aidyc_integration.py -q
python -m pytest shell/tests/test_tasks.py -q
```

## Persistencia AIDYC (detalle)

- Path: `datos/{padre_db}/tareas.json`
- Lock: `fcntl.flock` sobre `{tareas.json}.lock`
- Escritura atómica vía tempfile + `os.replace`
- API web `/api/tareas*` intacta (compatibilidad UI web)
