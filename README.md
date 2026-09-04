# Gwen

Gwen es un asistente personal privado para Telegram, construido con Python. La
primera versión conversa por texto y audio, conserva contexto reciente y guarda
recuerdos únicamente cuando el usuario se lo pide.

## Capacidades iniciales

- Texto de Telegram → respuesta de texto.
- Nota de voz → transcripción, respuesta de Claude y nota de voz.
- Acceso restringido a un único Telegram user ID.
- Historial reciente y recuerdos persistentes separados.
- Comandos `/memories`, `/remember`, `/forget` y `/privacy`.
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
python -m gwen
```

Para obtener tu Telegram user ID puedes escribirle a `@userinfobot`. Gwen
ignorará silenciosamente a cualquier otro usuario.

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
- `/privacy`: explicar qué se almacena.

## Seguridad

Nunca confirmes credenciales, contraseñas, claves API ni datos bancarios como
recuerdos. Los secretos viven exclusivamente en variables de entorno. Antes de
un despliegue público se añadirán migraciones, copias de seguridad y cifrado de
los datos almacenados.

## Próximas fases

Consulta [docs/ROADMAP.md](docs/ROADMAP.md). Recordatorios, resúmenes y alertas
proactivas están previstos, pero no se activan hasta definir horarios y fuentes.

