# Mailko para Python

La librería oficial de [Mailko](https://mailko.co) para Python: envía correos por la API `/v1`, verifica los
webhooks y, en Django, reemplaza el backend de correo con una línea.

```bash
pip install mailko            # solo el cliente
pip install "mailko[django]"  # con la integración de Django
```

Python 3.10 o superior. Django 4.2 o superior para `mailko.contrib.django`.

## Antes de empezar

En el panel de Mailko, dentro del proyecto desde el que vas a enviar:

1. **Verifica tu dominio** (Dominios → Agregar): publica los registros DNS que te muestra.
2. **Crea una API key** (Configuración → Desarrolladores) con el permiso `send`. Empieza por `mk_live_`; una
   `mk_test_` valida todo y no entrega nada, útil en pruebas.
3. Si vas a recibir webhooks, **crea el webhook** con la URL de tu servidor y guarda su secreto (`whsec_…`):
   solo se muestra una vez.

## Enviar un correo

```python
import mailko

client = mailko.Mailko("mk_live_…")

email = client.emails.send(
    from_="Tienda <pedidos@tienda.co>",
    to="cliente@correo.com",
    subject="Tu pedido #1234",
    html="<p>Gracias por tu compra</p>",
    text="Gracias por tu compra",
    attachments=[mailko.Attachment("factura.pdf", pdf_bytes)],
    tags={"pedido": "1234"},
    idempotency_key="pedido-1234-confirmacion",
)
print(email["id"], email["status"])  # msg_…  queued
```

- `from_` lleva guion bajo porque `from` es palabra reservada de Python.
- Los adjuntos van como `mailko.Attachment(nombre, bytes)`, una tupla `(nombre, bytes, tipo)` o el `dict` de la
  API con el contenido en base64. El tipo se adivina por el nombre si no lo das.
- `tags` vuelve en cada webhook de ese correo: úsalo para encontrar tu registro.
- Hasta 50 destinatarios entre `to`, `cc` y `bcc`, 20 adjuntos y 10 MB por correo. La librería lo revisa antes
  de llamar y lanza `mailko.MessageRejectedError` con la explicación.

Varios correos de una vez (hasta 100, todos o ninguno):

```python
client.emails.batch([
    mailko.build_email(from_="pedidos@tienda.co", to="ana@correo.com", subject="Hola", text="…"),
    mailko.build_email(from_="pedidos@tienda.co", to="luis@correo.com", subject="Hola", text="…"),
])
```

Con una plantilla del panel:

```python
client.emails.send(
    from_="pedidos@tienda.co",
    to="ana@correo.com",
    template={"id": "bienvenida", "variables": {"nombre": "Ana"}},
)
```

## Reintentos e idempotencia

Cada escritura lleva una `Idempotency-Key`: la que pases o una nueva. La librería reintenta (2 veces por
defecto) **con la misma clave** solo cuando es seguro: Mailko no respondió, la misma clave todavía se procesa
(`409`), demasiadas peticiones (`429`, esperando lo que diga `Retry-After` hasta `max_retry_after`) o un error
de Mailko (`5xx`). Así un reintento nunca envía el correo dos veces.

Si reintentas por tu cuenta (una tarea de Celery), pasa **siempre la misma** `idempotency_key` para el mismo
correo, por ejemplo `f"pedido-{pedido.id}-confirmacion"`, **con exactamente el mismo contenido**:

- Misma clave y mismo cuerpo: Mailko responde lo mismo que la primera vez y no envía de nuevo.
- Misma clave y otro cuerpo (otro asunto, otra etiqueta, otro adjunto): `IdempotencyError` con code
  `idempotency_key_reused`. No lo trates como «ya enviado»: puede ser otro correo. Si tu reintento cambia algo
  del cuerpo (un id de registro nuevo en `tags`, por ejemplo), reutiliza el del primer intento.
  Mailko también compara la llave: la misma clave enviada con otra API key da el mismo error.
- Mailko no guarda la respuesta de un `5xx` ni de un `402`, `403`, `409`, `423` o `429`: un reintento con la
  misma clave se vuelve a evaluar (por ejemplo, después de recargar saldo o de verificar el dominio).

```python
client = mailko.Mailko(
    "mk_live_…",
    timeout=(5, 30),        # segundos: conectar, leer
    max_retries=2,
    max_retry_after=10,     # un Retry-After más largo se lanza como RateLimitError
)
```

## Errores

Todo error de Mailko trae `message` (una frase en español para quien envió), `code` (estable: es lo que debe
mirar tu código), `status`, `request_id` (dáselo a soporte) y, en `validation_error`, `fields`.

| Clase | Cuándo |
|---|---|
| `MessageRejectedError` | La librería lo rechazó antes de llamar (sin destinatario, muy grande…). |
| `ConfigurationError` | Falta la llave, no es de Mailko o `base_url` no es https. Reintentar no sirve. Es también `MessageRejectedError` y `ValueError`. |
| `BadRequestError` | 400, 413, 415, 422: la petición está mal; corrígela. |
| `AuthenticationError` | 401: la llave falta, está mal, se revocó o venció. |
| `PaymentRequiredError` | 402: el plan o el saldo no alcanza (`insufficient_balance`…). |
| `PermissionDeniedError` | 403: `missing_scope`, `domain_not_verified`, `workspace_paused`… |
| `NotFoundError` | 404. |
| `ConflictError` | 409 (`confirmation_required`, `suppression_not_removable`…). |
| `IdempotencyError` | La clave sigue en proceso o se usó con otra petición. |
| `RateLimitError` | 429; `retry_after` dice cuántos segundos esperar. |
| `ServerError` | 5xx: problema de Mailko; reintenta con la misma clave. |
| `APIConnectionError` | Mailko no respondió. |

Todas heredan de `mailko.MailkoError`.

```python
try:
    client.emails.send(...)
except mailko.PermissionDeniedError as exc:
    if exc.code == "domain_not_verified":
        ...
except mailko.MailkoError as exc:
    logger.warning("Mailko: %s", exc)
```

Un `202` con `"status": "suppressed"` significa que Mailko tomó la petición y no envió nada: todas las
direcciones están en la lista de supresión del proyecto (rebotaron o se quejaron antes).

## Webhooks

Mailko firma cada aviso en la cabecera `Mailko-Signature`. Verifica el **cuerpo crudo**, tal como llegó:

```python
event = mailko.construct_event(
    request.body,                              # bytes, sin volver a serializar
    request.headers.get("Mailko-Signature"),
    "whsec_…",                                 # o una lista: varios proyectos o una rotación
)
if event["type"] == "email.bounced":
    email = event["data"]["object"]
    ...
```

`construct_event` lanza `mailko.SignatureVerificationError` si la firma falta, no corresponde o tiene más de
5 minutos. Un evento puede llegar repetido: deduplica por `event["id"]`.

## Django

```python
# settings.py
EMAIL_BACKEND = "mailko.contrib.django.EmailBackend"
MAILKO_API_KEY = env("MAILKO_API_KEY")
MAILKO_WEBHOOK_SECRET = env("MAILKO_WEBHOOK_SECRET")   # whsec_…; varios separados por coma

# urls.py
path("mailko/", include("mailko.contrib.django.urls")),   # → /mailko/webhook/
```

Con `MAILERS` (Django 6.1 en adelante) es igual, y cada mailer puede llevar su propia llave:

```python
MAILERS = {
    "default": {"BACKEND": "mailko.contrib.django.EmailBackend"},   # usa MAILKO_API_KEY
    "negocios": {"BACKEND": "mailko.contrib.django.EmailBackend", "OPTIONS": {"api_key": env("MAILKO_NEGOCIOS_KEY")}},
}
```

No necesita `INSTALLED_APPS` ni migraciones. Desde ahí, `send_mail`, `EmailMessage`, `EmailMultiAlternatives` y
cualquier librería que envíe por Django salen por Mailko: el cuerpo y su alternativa HTML, `cc`, `bcc`,
`reply_to`, los adjuntos y las cabeceras que Mailko permite (`List-Unsubscribe`, `References`, `In-Reply-To`…;
las demás se omiten, porque Mailko rechazaría el correo entero).

Lo que Django no sabe decir va como atributo del mensaje:

```python
message = EmailMultiAlternatives(subject, text, "pedidos@tienda.co", ["ana@correo.com"])
message.attach_alternative(html, "text/html")
message.mailko_tags = {"pedido": "1234"}
message.mailko_idempotency_key = f"pedido-{pedido.id}-confirmacion"   # la misma en cada reintento
message.mailko_stream = "marketing"                                    # por defecto, transactional
message.send()
print(message.mailko_id, message.mailko_status)
```

La `mailko_idempotency_key` nombra **un** correo: en cada reintento va con el mismo contenido (Mailko responde
lo mismo que la primera vez y no lo envía de nuevo). Si la misma clave llega con otro contenido, Mailko lo
rechaza con `IdempotencyError`, porque podría ser otro correo.

Si todas las direcciones de un mensaje están en la lista de supresión, ese mensaje no sale, los demás del lote
sí, y al final se lanza `mailko.SuppressedError` (salvo con `fail_silently`). Una alternativa que no sea HTML ni
texto (`text/calendar`, por ejemplo) se omite: la API de Mailko envía solo `html` y `text`.

### Qué pasó con cada correo

La vista del webhook verifica la firma, ignora los eventos repetidos y emite señales:

```python
from django.dispatch import receiver
from mailko.contrib.django.signals import email_event


@receiver(email_event)
def on_mailko_email(sender, event, type, email, **kwargs):
    pedido = email.get("tags", {}).get("pedido")
    if type == "email.bounced" and email["event"].get("bounce_type") == "permanent":
        ...
```

Lo que responde la vista:

| Respuesta | Cuándo |
|---|---|
| `200` | Procesado, o ya procesado antes (`"duplicate": true`). |
| `400` | La firma falta, no corresponde o venció. |
| `404` | `MAILKO_WEBHOOK_SECRET` no está configurado: la URL queda cerrada. |
| `409` | Otra entrega del mismo evento se está procesando en este momento (la reserva dura hasta 5 minutos); Mailko lo reintenta más tarde. Si tu receptor puede tardar más, hazlo idempotente. |
| `413` | El cuerpo pasa de `MAILKO_WEBHOOK_MAX_BYTES` (se lee como máximo un byte de más). Si subes ese límite, súbelo también en `DATA_UPLOAD_MAX_MEMORY_SIZE` si tu servidor lo aplica. |
| `500` | Tu receptor lanzó una excepción: el evento no queda marcado y Mailko lo reenvía más tarde. |

### Configuración

| Variable | Qué es | Por defecto |
|---|---|---|
| `MAILKO_API_KEY` | La llave del proyecto. | — |
| `MAILKO_API_KEYS_BY_DOMAIN` | `{"negocios.tienda.co": "mk_live_…"}`: otro proyecto para lo que sale de ese dominio. | `{}` |
| `MAILKO_API_URL` | La dirección de la API. | `https://api.mailko.co` |
| `MAILKO_TIMEOUT` | Segundos, o `(conectar, leer)`. | `(5, 30)` |
| `MAILKO_MAX_RETRIES` | Reintentos dentro de un envío. | `2` |
| `MAILKO_MAX_RETRY_AFTER` | El `Retry-After` más largo que se espera dentro de un envío. | `10` |
| `MAILKO_WEBHOOK_SECRET` | `whsec_…`; una lista o separados por coma para varios proyectos. | — |
| `MAILKO_WEBHOOK_TOLERANCE` | Segundos que vale una firma. | `300` |
| `MAILKO_WEBHOOK_DEDUP_SECONDS` | Cuánto se recuerda un evento ya procesado; `0` lo apaga. Usa la caché `default` de Django: con varios procesos necesita una compartida (Redis, Memcached, base de datos), no `LocMemCache`, o cada proceso recuerda solo lo suyo. | `86400` |
| `MAILKO_WEBHOOK_MAX_BYTES` | El cuerpo más grande que acepta la vista del webhook. | `262144` (256 KB) |

## Todo lo demás de la API

Cada recurso de `/v1` cuelga del cliente y responde el JSON de Mailko como `dict`. Las listas responden una
página (`{"data": [...], "pagination": {...}}`) y `list_all()` las recorre todas:

```python
client.me.retrieve()
client.domains.create("tienda.co")              # los registros DNS para publicar
client.domains.verify(domain_id)
for email in client.emails.list_all(status="bounced"):
    ...
client.suppressions.create("ana@correo.com", detail="Pidió no recibir más")
client.webhook_endpoints.create(url="https://tienda.co/mailko/webhook/", topics=["email.bounced"])
client.campaigns.send(campaign_id, confirm=True)
```

La referencia completa de la API está en [api.mailko.co/docs](https://api.mailko.co/docs/).

## Desarrollo

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```

Una versión nueva se publica en PyPI sola al subir su tag (`vX.Y.Z`): ver `.github/workflows/publish.yml`.

## Licencia

MIT © Bithaus SAS.
