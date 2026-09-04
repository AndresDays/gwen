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

Ruta del proyecto: `C:\Programacion\gwen`.

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
- `/remember`, `/memories`, `/forget`, `/privacy` y `/start` existen.
- La nota de voz se descarga y transcribe correctamente.
- La primera voz seleccionada era de Voice Library y devolvía HTTP 402 en el
  plan gratuito. El usuario creó una voz con Voice Design, cambió su Voice ID y
  la respuesta hablada ya funciona.
- Se añadió fecha/hora dinámica de Guatemala al prompt para impedir errores como
  calcular 20 años para una persona nacida el 4 de abril de 2004 en 2026.
- Si TTS devuelve HTTP 402, Gwen responde por texto y explica que falta acceso o
  saldo, en lugar de quedarse silenciosa.
- Última validación conocida: 3 pruebas aprobadas y Ruff limpio.

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

Pendiente prioritario: hacer permanente esa configuración en `main.py` y crear
scripts seguros `start-gwen.ps1` y `stop-gwen.ps1`. Antes de leer un log,
comprobar que no contiene un patrón de token de Telegram y nunca mostrar URLs
autenticadas.

## Deuda técnica conocida

- `tzdata` se instaló manualmente en `.venv` porque Windows no incluía la zona
  IANA `America/Guatemala`; debe añadirse a `pyproject.toml` para instalaciones
  reproducibles.
- El proyecto fue movido después de crear `.venv`; se reinstaló editable en la
  ruta nueva y funciona.
- Git está inicializado, pero todavía no existe el primer commit.
- El historial se incluye en cada llamada; falta resumen/compactación y un
  comando `/new` que borre conversación reciente sin borrar recuerdos.
- La extracción de memoria natural todavía depende de `/remember`; falta
  permitir frases como “recuerda que...” de forma segura.
- Faltan migraciones, backup, cifrado, límites de costos y manejo amplio de
  errores/rate limits.
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

1. Añadir `tzdata` a dependencias y hacer permanente el logging seguro.
2. Crear scripts de inicio/detención y probarlos sin exponer secretos.
3. Agregar `/new` y pruebas del fallback de audio y fecha con reloj inyectable.
4. Hacer el primer commit después de revisar que `.env` y `gwen.db` no estén
   versionados.
5. Recabar datos de iPhone/Mac/Xcode.
6. Diseñar y construir el MVP nativo de voz continua.
7. Después, trabajador local para Claude Code y monitor financiero.

## Preferencias de colaboración

El usuario prefiere guía paso a paso, lenguaje sencillo y acciones concretas.
No asumir experiencia previa con Telegram bots, variables de entorno, Apple
signing ni herramientas de desarrollo. Nunca pedir que pegue secretos en el chat.
