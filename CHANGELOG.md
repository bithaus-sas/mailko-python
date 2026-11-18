# Changelog

Todos los cambios de esta librería. Formato [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
versiones [SemVer](https://semver.org/lang/es/).

## [Unreleased]

### Added

- `saas-bump.yml`: cuando una versión queda publicada en PyPI, abre un PR en cada SaaS que usa el SDK (la
  variable `SAAS_REPOS` del repositorio; por defecto `klopi-api`) que mueve su pin `mailko[...]==X.Y.Z` y trae
  las notas de esa versión. Necesita el secreto `SAAS_REPOS_TOKEN`.

## [0.1.1] - 2026-11-17

### Added

- `mailko.ConfigurationError` (también `ValueError`): una llave que falta o no es de Mailko, o un `base_url` sin
  https. El backend de Django ya no lanza un `ValueError` suelto, que no se distinguía de un fallo de red.
- `MAILKO_WEBHOOK_MAX_BYTES` (256 KB): la vista del webhook rechaza con 413 un cuerpo más grande antes de leerlo.

### Changed

- El webhook reserva cada evento antes de procesarlo (hasta 5 minutos): una segunda entrega del mismo evento
  mientras la primera corre recibe 409 y Mailko la reintenta después, en vez de procesarse dos veces a la vez. Lo
  que 0.1.0 dejó marcado en la caché sigue contando como ya procesado.
- Si la caché falla al marcar un evento ya procesado, la vista responde 200 igual (antes, 500 y Mailko lo
  reenviaba).
- Sin `MAILKO_API_KEY`, el backend lanza `ConfigurationError`, que sigue siendo `MessageRejectedError`: el código
  que lo capturaba en 0.1.0 no cambia.

### Docs

- Idempotencia: el reintento debe llevar el mismo cuerpo; qué estados no guarda Mailko (5xx, 402, 403, 409, 423,
  429) y por qué `idempotency_key_reused` no significa «ya enviado».
- Las respuestas de la vista del webhook (200, 400, 404, 409, 413, 500).

## [0.1.0] - 2026-11-17

### Added

- Cliente de la API `/v1` (versión `2026-10-01`): correos (`send`, `batch`, `list`, `retrieve`), dominios,
  supresiones, plantillas, uso, webhooks, contactos, listas, segmentos, campañas, importaciones y «Conectar
  Mailko» para apps socias.
- `Idempotency-Key` en cada escritura, la misma en cada reintento; reintentos solo cuando es seguro (sin
  respuesta, `409 idempotency_key_in_progress`, `429` con `Retry-After`, `5xx`).
- Un error por estado HTTP (`AuthenticationError`, `PermissionDeniedError`, `RateLimitError`…) con el `code`,
  el `message` en español y el `request_id` de Mailko.
- `build_email` y `check_email`: el cuerpo de un correo y los límites de Mailko revisados antes de llamar.
- `verify_signature` y `construct_event` para los webhooks (`Mailko-Signature`), con varios secretos a la vez.
- Django: `mailko.contrib.django.EmailBackend` (también como mailer de `MAILERS` en Django 6.1), la vista del
  webhook (`mailko.contrib.django.urls`) y las señales `email_event` y `webhook_received`.

### Security

- La llave solo viaja por https (http únicamente hacia un host de desarrollo) y nunca se siguen redirecciones.
