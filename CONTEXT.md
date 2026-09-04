# Contexto de continuidad — Gwen

Actualizado: 4 de septiembre de 2026, zona horaria `America/Guatemala`.

## Instrucción para el siguiente chat

Continúa el desarrollo de Gwen desde el estado real del repositorio. Antes de
cambiar código, revisa este archivo, `README.md`, `docs/ROADMAP.md`, `git status`
y las pruebas. Nunca leas, imprimas, registres ni solicites el contenido de
`.env`. Valida secretos solamente como presentes/ausentes y enmascara errores.

## Objetivo del usuario

Gwen es una asistente personal privada, cálida, directa, inteligente, proactiva
y con humor moderado. Debe hablar español e inglés. Su propósito se refinó para
no duplicar funciones que ChatGPT ya ofrece:

- memoria estructurada de proyectos, personas, preferencias y compromisos;
- delegación futura de programación a Claude Code usando el plan Claude Max;
- integración futura con un monitor financiero;
- automatizaciones personalizadas;
- Telegram como interfaz de texto, audio y alertas;
- meta principal: app nativa para el iPhone personal del usuario con una sesión
  de voz continua similar al modo de voz de ChatGPT/Claude.

## Decisiones

- Nombre: Gwen.
- Backend: Python.
- LLM actual: Anthropic Claude API.
- Voz/transcripción actual: ElevenLabs.
- Interfaz actual: bot privado de Telegram.
- Usuario único mediante allowlist de Telegram user ID.
- Texto produce texto; nota de voz produce audio cuando TTS está disponible.
- La memoria se guarda cuando el usuario lo pide; no se deben guardar claves,
  contraseñas, tokens ni datos bancarios completos.
- Desarrollo local con SQLite; PostgreSQL previsto para producción.
- Zona horaria: `America/Guatemala`.
- Hosting todavía no elegido.
- El usuario tiene Claude Max, pero Claude API se factura por separado.

## Ubicación y estructura

Ruta actual del proyecto: `C:\Users\jadr7\gwen`.

- `src/gwen/assistant.py`: prompt, contexto temporal y llamadas a Claude.
- `src/gwen/bot.py`: handlers de Telegram y fallback de audio.
- `src/gwen/voice.py`: ElevenLabs STT/TTS.
- `src/gwen/models.py`: mensajes y recuerdos.
- `src/gwen/repository.py`: persistencia.
- `src/gwen/main.py`: arranque.
- `.env`: secretos locales; ignorado por Git; jamás mostrar su contenido.
- `gwen.db`: base SQLite local; ignorada por Git.
- `tests/`: pruebas automatizadas.

## Estado funcional comprobado

- Telegram, Anthropic y ElevenLabs fueron validados correctamente.
- El bot público es `@GwenPersonalAssistantBot`.
- Conversación escrita funciona.
- `/remember`, `/memories`, `/forget`, `/privacy`, `/new` y `/start` existen.
- `/new` borra solo el historial reciente del usuario y conserva sus recuerdos.
- La nota de voz se descarga y transcribe correctamente.
- La primera voz seleccionada era de Voice Library y devolvía HTTP 402 en el
  plan gratuito. El usuario creó una voz con Voice Design, cambió su Voice ID y
  la respuesta hablada ya funciona.
- Se añadió fecha/hora dinámica de Guatemala al prompt para impedir errores como
  calcular 20 años para una persona nacida el 4 de abril de 2004 en 2026.
- Si TTS devuelve HTTP 402, Gwen responde por texto y explica que falta acceso o
  saldo, en lugar de quedarse silenciosa.
- `tzdata` ya está declarado como dependencia.
- El logging seguro de `httpx` y `httpcore` ya es permanente.
- Existen `start-gwen.ps1` y `stop-gwen.ps1` con manejo de PID.
- Se refinó el prompt para una conversación más natural, menos estructurada y
  con humor moderado únicamente cuando encaje.
- La memoria natural reconoce “recuerda que…” y rechaza posibles secretos.
- El historial se compacta localmente en un resumen persistente sin otra llamada al LLM.
- Hay límites diarios configurables para tokens de Claude, segundos de STT y
  caracteres de TTS, consultables con `/usage`.
- Los fallos y rate limits se convierten en mensajes seguros sin filtrar detalles.
- `backup-gwen.ps1` crea backups consistentes de SQLite con retención configurable.
- Existe una interfaz web privada en `http://127.0.0.1:8765`, con texto, grabación
  de voz, reproducción de TTS, historial compartido, `/new` visual y consumo.
- Se inicia y detiene con `start-gwen-web.ps1` y `stop-gwen-web.ps1`.
- El audio web usa Scribe v2, MIME normalizado, captura con reducción de ruido y
  muestra la transcripción detectada antes de la respuesta.
- La interfaz web tiene un diseño futurista responsive sin recursos remotos.
- La web ofrece una sesión de escucha continua con VAD local, cierre automático por
  silencio, respuesta hablada y reanudación de escucha; conserva grabación manual.
- La voz continua usa WebSocket local y Scribe v2 Realtime con PCM de 16 kHz;
  muestra transcripción parcial, transmite la respuesta de Claude y sintetiza frases con
  TTS Flash a 22.05 kHz/32 kbps mientras la respuesta aún se genera.
- El usuario puede interrumpir el audio de Gwen hablando; la grabación manual por HTTP
  permanece como respaldo.
- Última validación conocida: 23 pruebas aprobadas, Ruff y JavaScript limpios.

## Incidente de seguridad resuelto

En el primer arranque, el logger INFO de `httpx` imprimió la URL de Telegram,
que contiene el token. El usuario revocó el token, creó otro y actualizó `.env`.
Los logs fueron limpiados. Anthropic y ElevenLabs no se expusieron.

No iniciar Gwen con logging INFO de `httpx`. Actualmente se ha usado este
arranque seguro:

```powershell
Set-Location C:\Programacion\gwen
.\.venv\Scripts\python.exe -c "import logging; logging.getLogger('httpx').setLevel(logging.WARNING); logging.getLogger('httpcore').setLevel(logging.WARNING); from gwen.main import run; run()"
```

La configuración ya es permanente en `main.py` y existen scripts seguros
`start-gwen.ps1` y `stop-gwen.ps1`. Antes de leer un log,
comprobar que no contiene un patrón de token de Telegram y nunca mostrar URLs
autenticadas.

## Deuda técnica conocida

- `tzdata` está declarado en `pyproject.toml` para instalaciones reproducibles.
- El proyecto fue movido después de crear `.venv`; se reinstaló editable en la
  ruta nueva y funciona.
- Git está inicializado y contiene commits locales; todavía no se configuró un remoto.
- El historial reciente se compacta automáticamente y `/new` permite borrarlo sin
  borrar recuerdos.
- La memoria natural y `/remember` rechazan patrones comunes de secretos; esta
  protección es preventiva y no sustituye el cuidado del usuario.
- Faltan migraciones formales, cifrado y backups externos/off-site.
- El proceso local solo funciona mientras la computadora está encendida.

## App nativa iPhone — dirección acordada

- Solo se necesita inicialmente para el iPhone del usuario.
- El usuario tiene una Mac, no tiene membresía Apple Developer de pago y acepta
  instalar una build de desarrollo.
- Comenzar gratis con Apple Personal Team; la instalación expira aproximadamente
  cada 7 días y debe renovarse desde Xcode.
- Tecnología propuesta: React Native + Expo development build.
- ElevenLabs React Native SDK + LiveKit/WebRTC; no funciona en Expo Go.
- Experiencia: un toque para iniciar, escucha continua, turn-taking, interrupción
  de Gwen, estados escuchando/pensando/hablando y botón finalizar.
- “Oye Gwen” con pantalla bloqueada no forma parte del primer MVP.
- ElevenAgents requiere permisos `Leído` y `Escribir` en la API key.
- El plan gratuito de ElevenAgents anuncia 15 minutos de llamadas; confirmar los
  precios nuevamente antes de tomar decisiones.
- La meta posterior es pagar Apple Developer Program (precio consultado: USD 99
  por año, sujeto a región/impuestos) cuando el prototipo demuestre valor.

Antes de crear la app faltan modelo de iPhone, versión de iOS, modelo/año de la
Mac, versión de macOS y confirmar si Xcode está instalado.

## Modo de programación futuro

Diseñar un trabajador local restringido que reciba tareas autorizadas y ejecute
Claude Code con `claude -p`, autenticado únicamente con Claude Max. Debe operar
solo en carpetas permitidas, pedir confirmación para acciones sensibles y nunca
aceptar comandos de shell crudos desde Telegram. El equipo debe estar encendido.
El consumo usa límites compartidos de Claude/Claude Code; no usar fallback PAYG
sin autorización explícita.

## Próximos pasos recomendados

1. Probar y refinar la interfaz web local con uso cotidiano.
2. Definir autenticación y hosting solo si se desea acceso fuera de esta computadora.
3. Recabar datos de iPhone/Mac/Xcode.
4. Diseñar y construir el MVP nativo de voz continua.
5. Después, trabajador local para Claude Code y monitor financiero.

## Preferencias de colaboración

El usuario prefiere guía paso a paso, lenguaje sencillo y acciones concretas.
No asumir experiencia previa con Telegram bots, variables de entorno, Apple
signing ni herramientas de desarrollo. Nunca pedir que pegue secretos en el chat.
