# Hoja de ruta de Gwen

## Fase 1 — conversación y memoria

- Bot privado de Telegram.
- Conversación bilingüe con Claude.
- Entrada y salida de audio con ElevenLabs.
- Historial reciente, compactación persistente y recuerdos explícitos o naturales.
- Límites diarios, fallbacks de proveedores y backups locales de SQLite.
- Contenedores para ejecución local y despliegue.

## Fase 2 — proactividad

- Recordatorios almacenados por Gwen.
- Zona horaria y ventanas de silencio configurables.
- Resumen diario programado.
- Cola de trabajos persistente para evitar alertas duplicadas.

Antes de implementarla hay que definir hora del resumen diario, ventanas de
silencio, repetición de recordatorios y política de reintentos.

## Fase 3 — herramientas

- Calendario con confirmación antes de crear, mover o borrar eventos.
- Correo en modo lectura/borrador primero.
- Búsqueda web con fuentes visibles.
- Integración con el monitor financiero cuando exista una interfaz estable.

## Fase 4 — operación

- Despliegue administrado.
- PostgreSQL, migraciones y copias de seguridad.
- Métricas, presupuesto mensual y límites de uso.
- Exportación y eliminación completa de datos.
