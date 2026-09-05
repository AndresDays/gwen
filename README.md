# Gwen

Gwen es un asistente personal privado para Telegram, construido con Python. La
primera versión conversa por texto y audio, conserva contexto reciente y guarda
recuerdos únicamente cuando el usuario se lo pide.

## Capacidades iniciales

- Texto de Telegram → respuesta de texto.
- Nota de voz → transcripción, respuesta de Claude y nota de voz.
- Acceso restringido a un único Telegram user ID.
- Historial reciente, compactación automática y recuerdos persistentes separados.
- Memoria natural con “recuerda que…”, protegida contra secretos comunes.
- Límite diario de tokens y respuestas seguras ante fallos temporales.
- Backups consistentes de SQLite con retención configurable.
- Comandos `/memories`, `/remember`, `/forget`, `/new` y `/privacy`.
- SQLite para desarrollo y PostgreSQL mediante `DATABASE_URL` para producción.
- Proveedores desacoplados para poder cambiar Claude o ElevenLabs después.

## Requisitos

- Python 3.12+
- Bot y token creados con [@BotFather](https://t.me/BotFather)
- API key y créditos de Anthropic Console
- API key de ElevenLabs para audio

## Puesta en marcha

```powershell
Copy-Item .env.example .env
# Completa .env sin compartir sus valores.
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
.\start-gwen.ps1
```

Para obtener tu Telegram user ID puedes escribirle a `@userinfobot`. Gwen
ignorará silenciosamente a cualquier otro usuario.

## Interfaz web privada

La interfaz web comparte conversación, recuerdos y límites con Telegram. Solo escucha
en `127.0.0.1`, por lo que no queda expuesta a la red local ni a Internet.

```powershell
.\start-gwen-web.ps1
```

Luego abre `http://127.0.0.1:8765`. Para detenerla:

```powershell
.\stop-gwen-web.ps1
```

Permite escribir, grabar manualmente o iniciar una sesión de escucha continua. El modo
web transmite PCM a Scribe Realtime, muestra la transcripción parcial, recibe la respuesta
de Claude por fragmentos y comienza a reproducir voz Flash por frases antes de que termine
la respuesta completa. Puedes interrumpir a Gwen hablando. La grabación manual se conserva
como respaldo y las claves nunca llegan al navegador.

### En macOS o Linux

Los scripts `.ps1` son solo lanzadores de Windows; el servidor es el mismo módulo:

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
cp .env.example .env   # completa tus claves
python -m gwen.web
```

Se detiene con Ctrl+C.

## Proyectos para tareas de código

Copia `gwen-code-workspaces.example.json` como `gwen-code-workspaces.json` y ajusta las
rutas y comandos a esta máquina. El archivo no se versiona, porque cada equipo tiene sus
propias rutas: en Windows `.venv\Scripts\python.exe` y `npm.cmd`; en macOS o Linux
`.venv/bin/python` y `npm`. Los workspaces cuya ruta no exista se ignoran con un aviso, y
si no queda ninguno válido Gwen arranca igual, sin tareas de código.

## Aplicación de escritorio

Ejecuta `install-gwen-desktop.ps1` una vez para crear el acceso directo **Gwen**
en el escritorio de Windows. Al abrirlo, inicia la interfaz local y la muestra en
una ventana independiente de Microsoft Edge, sin pestañas ni barra del navegador.
Las órdenes de programación se dan directamente en el chat normal, por texto o voz, sin abrir un panel separado. Gwen puede trabajar únicamente en los proyectos definidos en `gwen-code-workspaces.json`; `.env` y secretos están bloqueados. Puede acumular varios cambios pendientes dentro de una sesión protegida. Solo crea un commit cuando se solicita expresamente y las validaciones configuradas terminan correctamente. También funciona desde el iPhone después del login privado habitual; no se pide otra contraseña dentro de la app. La computadora debe permanecer encendida.
## Acceso privado desde iPhone

Gwen puede publicarse solo dentro de una red privada Tailscale, con HTTPS y sin abrir
puertos del router ni usar Tailscale Funnel. En la PC y el iPhone debe estar instalada
y conectada la app Tailscale con la misma cuenta. Luego, en la PC, ejecuta:

```powershell
.\setup-gwen-remote.ps1
```

El asistente pide una contraseña de al menos 12 caracteres sin mostrarla, guarda solo
un hash Scrypt y un secreto de sesión en `.gwen-web-auth.json` (ignorado por Git),
configura Tailscale Serve y reinicia la web. El modo remoto añade cookies Secure,
HttpOnly y SameSite, validación de origen para HTTP/WebSocket, bloqueo tras cinco
intentos fallidos en 15 minutos, allowlist de host, CSP, HSTS y respuestas sin caché.
La aplicación falla cerrada si se intenta activar sin HTTPS o sin autenticación completa.

Para retirar el acceso remoto, desactiva Tailscale Serve y elimina la configuración local:

```powershell
tailscale serve reset
Remove-Item .gwen-web-auth.json
.\stop-gwen-web.ps1
.\start-gwen-web.ps1
```
## Instalar como app en iPhone

Abre la URL privada de Gwen en Safari, toca **Compartir** y elige **Agregar a inicio**.
La PWA usa pantalla completa, safe areas y reconexión automática al recuperar Internet o
volver desde segundo plano. En **Métricas**, después del primer turno de voz, aparece el
tiempo real hasta transcripción, primer texto, primer audio y primer sonido. Estas mediciones
se guardan solo en el dispositivo y nunca incluyen el contenido de la conversación.
## Elegir la voz

1. En ElevenLabs abre **Voices → Explore**.
2. Filtra por español/inglés, acento, género y estilo conversacional.
3. Escucha las muestras y añade la voz elegida a **My Voices**.
4. Copia su **Voice ID** y colócalo como `ELEVENLABS_VOICE_ID` en `.env`.

La voz es configuración, no código: cambiar el ID cambia la voz de Gwen.

## Comandos

- `/start`: comprobar que Gwen está activa.
- `/remember <dato>`: guardar un recuerdo explícito.
- `/memories`: consultar los recuerdos guardados.
- `/forget <texto>`: borrar recuerdos coincidentes.
- `/new`: borrar la conversación reciente sin borrar recuerdos.
- `/usage`: consultar el consumo de tokens del día.
- `/privacy`: explicar qué se almacena.

## Operación local

- `./start-gwen.ps1`: iniciar Gwen en segundo plano.
- `./stop-gwen.ps1`: detener la instancia iniciada por el script.
- `./backup-gwen.ps1`: crear un backup consistente dentro de `backups/`.

Los backups locales se conservan durante 14 días por defecto. Ajusta
`BACKUP_RETENTION_DAYS` si necesitas otra ventana. `DAILY_TOKEN_LIMIT` controla el
tope diario de tokens, `DAILY_VOICE_SECONDS_LIMIT` y
`DAILY_TTS_CHARACTER_LIMIT` limitan ElevenLabs, y `MAX_INPUT_CHARS` evita
entradas excesivamente grandes.
## Seguridad

Nunca confirmes credenciales, contraseñas, claves API ni datos bancarios como
recuerdos. Los secretos viven exclusivamente en variables de entorno. Los backups locales ya están disponibles. Antes de un despliegue público todavía se
añadirán migraciones formales y cifrado de los datos almacenados.

## Próximas fases

Consulta [docs/ROADMAP.md](docs/ROADMAP.md). Recordatorios, resúmenes y alertas
proactivas están previstos, pero no se activan hasta definir horarios y fuentes.
